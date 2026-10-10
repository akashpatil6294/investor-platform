"""Persist financial metrics with full provenance.

Every saved row carries the reporting currency and unit, the fiscal period,
the extraction status, human-readable notes, and a per-metric ``metric_sources``
map ({value, unit, currency, page, quote, confidence, derived, ...}).

An all-null numeric extraction is NEVER saved as success: the caller must
mark the job failed and keep the document chunks for chat.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import text

from database.postgres_sql import get_engine
from ingestion.units import normalize_unit, unit_multiplier

load_dotenv()

NUMERIC_FIELDS = (
    "revenue", "net_income", "operating_income",
    "cash_flow", "total_assets", "total_liabilities",
)
# DB column for operating cash flow predates this module's naming.
DB_COLUMNS = {
    "revenue": "revenue",
    "net_income": "net_income",
    "operating_income": "operating_income",
    "cash_flow": "cash_flow_from_operations",
    "total_assets": "total_assets",
    "total_liabilities": "total_liabilities",
}


def _db_value(value) -> float | None:
    if value is None:
        return None
    try:
        import math
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def build_record(
    company: str,
    year: int,
    metrics: dict[str, Any],
    *,
    currency: str | None = None,
    unit: str | None = None,
    period_start: str | date | None = None,
    period_end: str | date | None = None,
    basis: str | None = None,
    extraction_status: str | None = None,
    extraction_notes: list[str] | None = None,
    metric_sources: dict | None = None,
    source_file: str | None = None,
    qualitative: dict | None = None,
    source_type: str = "pipeline",
    version: int = 1,
) -> dict:
    """Assemble the financial_metrics record without writing it."""
    unit_canonical = normalize_unit(unit)
    multiplier = unit_multiplier(unit)

    if extraction_status is None or not any(_db_value(metrics.get(f)) is not None for f in NUMERIC_FIELDS):
        present = sum(1 for f in NUMERIC_FIELDS if _db_value(metrics.get(f)) is not None)
        if present == 0:
            extraction_status = "failed"
        elif present < len(NUMERIC_FIELDS):
            extraction_status = "partial"
        else:
            extraction_status = "ok"

    record = {
        "company": company,
        "year": int(year),
        "version": int(version),
        **{DB_COLUMNS[f]: _db_value(metrics.get(f)) for f in NUMERIC_FIELDS},
        "currency": currency,
        "unit": unit_canonical,
        "unit_multiplier": multiplier,
        "period_start": period_start,
        "period_end": period_end,
        "basis": basis,
        "extraction_status": extraction_status,
        "extraction_notes": json.dumps(extraction_notes or []),
        "metric_sources": json.dumps(metric_sources or {}),
        "source_file": source_file,
        "source_type": source_type,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    for key in ("risk_factors", "growth_drivers"):
        items = (qualitative or {}).get(key)
        if items is None:
            items = metrics.get(key) or []
        if isinstance(items, str):
            items = [items]
        record[key] = json.dumps(items)
    return record


def save_metrics(
    company: str,
    year: int,
    metrics: dict[str, Any],
    *,
    currency: str | None = None,
    unit: str | None = None,
    period_start: str | date | None = None,
    period_end: str | date | None = None,
    basis: str | None = None,
    extraction_status: str | None = None,
    extraction_notes: list[str] | None = None,
    metric_sources: dict | None = None,
    source_file: str | None = None,
    qualitative: dict | None = None,
    source_type: str = "pipeline",
    duplicate_action: str = "replace",
) -> dict:
    """Upsert one (company, year) metrics row.

    ``duplicate_action`` decides what happens when rows already exist for the
    same company and year:
      - ``replace``  archive the old rows (financial_metrics_history, no
        silent loss) and write version 1;
      - ``keep_both`` keep the old rows and store this one as the next
        version.
    Raises RuntimeError when every numeric KPI is null — an all-null row is a
    failed extraction, not a fact about the company.
    """
    if duplicate_action not in ("replace", "keep_both"):
        raise ValueError(f"Unsupported duplicate_action: {duplicate_action!r}")
    present = sum(1 for f in NUMERIC_FIELDS if _db_value(metrics.get(f)) is not None)
    if present == 0 and (extraction_status or "failed") != "failed":
        raise RuntimeError(
            "Refusing to save an all-null metrics row as success for "
            f"{company} {year}; mark the job failed instead."
        )

    record = build_record(
        company, year, metrics,
        currency=currency, unit=unit,
        period_start=period_start, period_end=period_end, basis=basis,
        extraction_status=extraction_status, extraction_notes=extraction_notes,
        metric_sources=metric_sources, source_file=source_file,
        qualitative=qualitative, source_type=source_type,
    )

    engine = get_engine()
    with engine.begin() as conn:
        existing = conn.execute(text(
            "SELECT version FROM financial_metrics "
            "WHERE lower(company) = lower(:company) AND year = :year"
        ), {"company": company, "year": int(year)}).scalars().all()
        if existing and duplicate_action == "keep_both":
            record["version"] = max(existing) + 1
        elif existing:
            archive_company_year(company, year, reason="Replaced by a new ingestion.",
                                 conn=conn)
            record["version"] = 1

        columns = ", ".join(record)
        jsonb_fields = ("extraction_notes", "metric_sources", "risk_factors", "growth_drivers")
        params = ", ".join(
            f"CAST(:{name} AS jsonb)" if name in jsonb_fields else f":{name}"
            for name in record
        )
        updates = ", ".join(
            f"{name} = EXCLUDED.{name}" for name in record if name not in ("company", "year", "version")
        )
        conn.execute(text(
            f"INSERT INTO financial_metrics ({columns}) VALUES ({params}) "
            f"ON CONFLICT (company, year, version) DO UPDATE SET {updates}"
        ), record)
    return record


def archive_company_year(company: str, year: int, *, reason: str,
                         conn=None) -> int:
    """Move every current row for (company, year) into the history table.

    Pass ``conn`` (an open transaction) to archive atomically with another
    write; otherwise a dedicated transaction is used."""
    if conn is None:
        with get_engine().begin() as conn:
            return archive_company_year(company, year, reason=reason, conn=conn)
    result = conn.execute(text("""
        WITH moved AS (
            DELETE FROM financial_metrics
            WHERE lower(company) = lower(:company) AND year = :year
            RETURNING *
        )
        INSERT INTO financial_metrics_history
            SELECT moved.*, now(), :reason FROM moved
        RETURNING id
    """), {"company": company, "year": int(year), "reason": reason})
    return len(result.scalars().all())


def list_versions(company: str, year: int) -> list[dict]:
    """Stored versions for a (company, year) — used by duplicate handling."""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT company, year, version, source_file, extraction_status, updated_at "
            "FROM financial_metrics WHERE lower(company) = lower(:company) AND year = :year "
            "ORDER BY version"
        ), {"company": company, "year": int(year)}).mappings().all()
    return [dict(row) for row in rows]


if __name__ == "__main__":
    save_metrics(
        company="Apple",
        year=2024,
        metrics={
            "revenue": 391035, "net_income": 93736, "operating_income": 123216,
            "cash_flow": 118254, "total_assets": 364980, "total_liabilities": 308030,
        },
        currency="USD", unit="million", period_end="2024-09-28",
        basis="consolidated", extraction_status="ok",
        extraction_notes=["Legacy self-test"],
        metric_sources={"revenue": {"value": 391035, "page": 1, "quote": "Total net sales"}},
        qualitative={
            "risk_factors": [{"title": "Demo", "detail": "Self-test row", "page": 1}],
            "growth_drivers": [],
        },
    )
