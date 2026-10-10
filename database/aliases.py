"""Company-name canonicalisation backed by the ``company_aliases`` table.

"Flipkart Internet Private Limited", "Flipkart" and "FLIPKART" are one entity
because the alias rows say so — nothing is hard-coded per company. The seed
aliases for the four legacy demo companies are inserted by
``database.migrations.run_migrations`` as data, and the user can add or edit
aliases from the review step (``POST /api/ingest/{job_id}/confirm`` accepts an
``aliases`` list).

A small TTL cache keeps chat's per-chunk canonicalisation from querying the
database for every retrieved chunk; it is cleared explicitly when aliases
change and expires quickly enough that a finished ingestion job is visible
without a restart.
"""

from __future__ import annotations

import threading
import time

from sqlalchemy import text

from database.postgres_sql import get_engine

CACHE_TTL_SECONDS = 30.0

_cache: dict[str, tuple[str, float]] = {}
_cache_lock = threading.Lock()
_table_ready = False


def _mark_table_ready() -> None:
    global _table_ready
    _table_ready = True


def table_ready() -> bool:
    return _table_ready


def resolve_alias(alias: str) -> str | None:
    """Canonical company for ``alias`` (case-insensitive), or None.

    Returns None (never raises) when the table is missing or the database is
    unreachable — callers keep working with uncanonicalised names."""
    if not alias:
        return None
    key = alias.strip().lower()
    if not key:
        return None
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[1] < CACHE_TTL_SECONDS:
            return hit[0]
    try:
        with get_engine().connect() as conn:
            row = conn.execute(text(
                "SELECT canonical FROM company_aliases WHERE alias = :alias"
            ), {"alias": key}).scalar()
    except Exception:
        return None
    if row is None:
        return None
    canonical = str(row)
    with _cache_lock:
        _cache[key] = (canonical, now)
    return canonical


def upsert_alias(alias: str, canonical: str) -> None:
    alias_key = (alias or "").strip().lower()
    canonical_clean = (canonical or "").strip()
    if not alias_key or not canonical_clean:
        raise ValueError("Alias and canonical company are both required.")
    with get_engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO company_aliases (alias, canonical, updated_at)
            VALUES (:alias, :canonical, now())
            ON CONFLICT (alias) DO UPDATE SET canonical = EXCLUDED.canonical,
                updated_at = now()
        """), {"alias": alias_key, "canonical": canonical_clean})
    clear_cache()


def list_aliases(canonical: str | None = None) -> dict[str, str]:
    try:
        with get_engine().connect() as conn:
            if canonical:
                rows = conn.execute(text(
                    "SELECT alias, canonical FROM company_aliases "
                    "WHERE canonical = :canonical ORDER BY alias"
                ), {"canonical": canonical.strip().lower()}).all()
            else:
                rows = conn.execute(text(
                    "SELECT alias, canonical FROM company_aliases ORDER BY alias"
                )).all()
    except Exception:
        return {}
    return {alias: canonical for alias, canonical in rows}


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def canonical_company(value: str | None) -> str:
    """Canonical stored name for ``value``; the input itself when unmapped.

    Degrades to the identity function when the alias table is unavailable so
    retrieval and chat never fail because of canonicalisation."""
    value = (value or "").strip()
    if not value:
        return value
    return resolve_alias(value) or value
