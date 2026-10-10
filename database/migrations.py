"""Idempotent schema migrations.

Run automatically at API startup and available standalone:
    python -m database.migrations

All changes are additive so existing rows keep working. Legacy rows get
currency/unit/period inferred generically from their own markdown source —
never from a company-name table.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path

from sqlalchemy import text

from database.postgres_sql import get_engine

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]

ADD_COLUMNS = [
    ("currency", "text"),
    ("unit", "text"),
    ("unit_multiplier", "double precision"),
    ("period_start", "date"),
    ("period_end", "date"),
    ("basis", "text"),
    ("extraction_status", "text"),
    ("extraction_notes", "jsonb"),
    ("metric_sources", "jsonb"),
    ("source_file", "text"),
    ("source_type", "text"),
    ("source_url", "text"),
    ("source_retrieved_at", "timestamptz"),
    ("updated_at", "timestamptz"),
]

INGEST_JOBS_DDL = """
CREATE TABLE IF NOT EXISTS ingest_jobs (
    job_id text PRIMARY KEY,
    kind text NOT NULL DEFAULT 'ingest',
    status text NOT NULL,
    stage text,
    filename text,
    company text,
    year integer,
    chunks_inserted integer,
    log jsonb NOT NULL DEFAULT '[]'::jsonb,
    detection jsonb,
    result jsonb,
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
)
"""

COMPANY_ALIASES_DDL = """
CREATE TABLE IF NOT EXISTS company_aliases (
    alias text PRIMARY KEY,
    canonical text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
)
"""

# Seed aliases for the four legacy demo rows as DATA, not code: the platform
# treats them like any other company alias and the user can edit them.
COMPANY_ALIASES_SEED = [
    ("apple", "Apple"), ("aapl", "Apple"),
    ("amazon", "Amazon"), ("amzn", "Amazon"),
    ("microsoft", "Microsoft"), ("msft", "Microsoft"),
    ("tesla", "Tesla"), ("tsla", "Tesla"),
]

HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS financial_metrics_history (
    LIKE financial_metrics INCLUDING DEFAULTS,
    archived_at timestamptz NOT NULL DEFAULT now(),
    archive_reason text
)
"""

CHAT_FEEDBACK_DDL = """
CREATE TABLE IF NOT EXISTS chat_feedback (
    id serial PRIMARY KEY,
    question text NOT NULL,
    answer_source text,
    helpful boolean NOT NULL,
    comment text,
    created_at timestamptz NOT NULL DEFAULT now()
)
"""


def _drop_legacy_company_year_unique(conn) -> None:
    """Drop a unique constraint/index covering exactly (company, year) so the
    versioned key (company, year, version) can replace it.

    NOTE: pg_attribute.attname is of type `name`, so array_agg returns name[].
    We cast to ::text so it can be compared with ARRAY['company', 'year'] (text[]).
    """
    rows = conn.execute(text("""
        SELECT con.conname
        FROM pg_constraint con
        WHERE con.conrelid = 'financial_metrics'::regclass
          AND con.contype = 'u'
          AND (SELECT array_agg(a.attname::text ORDER BY k.ord)
               FROM unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord)
               JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.attnum
              ) = ARRAY['company', 'year']
    """)).scalars().all()
    for name in rows:
        conn.execute(text(f'ALTER TABLE financial_metrics DROP CONSTRAINT "{name}"'))
    if rows:
        log.info("Dropped legacy unique constraint(s) on (company, year): %s", ", ".join(rows))


def run_migrations() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text(INGEST_JOBS_DDL))
        conn.execute(text(COMPANY_ALIASES_DDL))
        for alias, canonical in COMPANY_ALIASES_SEED:
            conn.execute(text(
                "INSERT INTO company_aliases (alias, canonical) VALUES (:alias, :canonical) "
                "ON CONFLICT (alias) DO NOTHING"
            ), {"alias": alias, "canonical": canonical})
        for column, ddl in ADD_COLUMNS:
            conn.execute(text(
                f"ALTER TABLE financial_metrics ADD COLUMN IF NOT EXISTS {column} {ddl}"
            ))
        conn.execute(text(
            "ALTER TABLE financial_metrics ADD COLUMN IF NOT EXISTS version integer "
            "NOT NULL DEFAULT 1"
        ))
        _drop_legacy_company_year_unique(conn)
        conn.execute(text(HISTORY_DDL))
        conn.execute(text(CHAT_FEEDBACK_DDL))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS financial_metrics_company_year_version_key "
            "ON financial_metrics (company, year, version)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ingest_jobs_created_idx ON ingest_jobs (created_at DESC)"
        ))
    _backfill_legacy_rows()
    log.info("Migrations up to date.")


# ---------------------------------------------------------------
# Generic legacy backfill
# ---------------------------------------------------------------


def _backfill_one(conn, row) -> None:
    from ingestion.units import infer_period_end, infer_unit_from_text, unit_multiplier

    company, year = row["company"], row["year"]
    source_name = row.get("source_file") or f"{year}_{company}.md"
    path = (ROOT / "data" / "markdown" / source_name)
    if not path.is_file():
        path = ROOT / "data" / "markdown" / f"{year}_{company}.md"
    currency = unit = None
    period_end = None
    if path.is_file():
        try:
            sample = path.read_text(encoding="utf-8", errors="ignore")[:40000]
            currency, unit = infer_unit_from_text(sample)
            period_end = infer_period_end(sample, fiscal_year=year)
        except OSError:
            pass
    numeric = [row.get(f) for f in (
        "revenue", "net_income", "operating_income",
        "cash_flow_from_operations", "total_assets", "total_liabilities",
    )]
    missing = sum(1 for v in numeric if v is None)
    status = "failed" if missing == 6 else "partial" if missing else "ok"
    notes = []
    if currency is None:
        notes.append("Legacy row: reporting currency could not be inferred from the source document.")
    conn.execute(text("""
        UPDATE financial_metrics
        SET currency = COALESCE(currency, :currency),
            unit = COALESCE(unit, :unit),
            unit_multiplier = COALESCE(unit_multiplier, :multiplier),
            period_end = COALESCE(period_end, :period_end),
            basis = COALESCE(basis, 'consolidated'),
            extraction_status = COALESCE(extraction_status, :status),
            extraction_notes = COALESCE(extraction_notes, CAST(:notes AS jsonb)),
            source_file = COALESCE(source_file, :source_file),
            source_type = COALESCE(source_type, 'pipeline')
        WHERE lower(company) = lower(:company) AND year = :year
    """), {
        "currency": currency, "unit": unit,
        "multiplier": unit_multiplier(unit) if unit else None, "period_end": period_end,
        "status": status, "notes": json.dumps(notes),
        "source_file": source_name, "company": company, "year": year,
    })


def _backfill_legacy_rows() -> None:
    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT company, year, revenue, net_income, operating_income,
                   cash_flow_from_operations, total_assets, total_liabilities,
                   currency, unit, period_end, source_file
            FROM financial_metrics
            WHERE currency IS NULL OR extraction_status IS NULL
        """)).mappings().all()
    for row in rows:
        with engine.begin() as conn:
            _backfill_one(conn, row)
    if rows:
        log.info("Backfilled %d legacy metric row(s).", len(rows))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migrations()
    print("Migrations complete.")