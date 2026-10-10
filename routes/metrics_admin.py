"""Metric correction and re-extraction endpoints.

PATCH /api/metrics/{company}/{year}  — correct a single metric by hand
    (source = 'manual') or accept a web-found value from the chat panel
    (source = 'web' with URL and retrieval date).
POST /api/metrics/{company}/{year}/reextract — re-run extraction against the
    stored markdown; runs as a background job.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from database.ingest_jobs import create_job, enqueue, get_job
from database.metrics import get_metrics_by_company_year
from database.postgres_sql import get_engine
from ingestion.units import normalize_currency, normalize_unit, unit_multiplier

router = APIRouter(tags=["metrics"])

KPI_FIELDS = ("revenue", "net_income", "operating_income", "cash_flow",
              "total_assets", "total_liabilities")
DB_COLUMNS = {
    "revenue": "revenue", "net_income": "net_income",
    "operating_income": "operating_income",
    "cash_flow": "cash_flow_from_operations",
    "total_assets": "total_assets", "total_liabilities": "total_liabilities",
}


class MetricPatch(BaseModel):
    metric: str
    value: float | None = Field(default=None, allow_inf_nan=False, description="Corrected value; omit to clear")
    unit: str | None = Field(default=None, max_length=16)
    currency: str | None = Field(default=None, max_length=8)
    quote: str | None = Field(default=None, max_length=400, description="Evidence quote or note")
    source: str = Field(default="manual", pattern="^(manual|web)$")
    url: str | None = Field(default=None, max_length=1000)
    reviewed: bool = Field(default=False, description="Mark the row as reviewed without changing values")


def _normalize_year_match(rows: list[dict], year: int) -> dict | None:
    for row in rows:
        if int(row["year"]) == int(year):
            return row
    for row in rows:
        pe = row.get("period_end")
        if pe and getattr(pe, "year", None) == int(year):
            return row
    return None


@router.patch("/metrics/{company}/{year}")
def patch_metric(company: str, year: int, patch: MetricPatch):
    if patch.metric not in KPI_FIELDS:
        raise HTTPException(400, f"Unknown metric '{patch.metric}'. Choose from {', '.join(KPI_FIELDS)}.")

    from urllib.parse import urlparse
    if patch.source == "web" and (not patch.url or urlparse(patch.url).scheme not in ("http", "https") or not urlparse(patch.url).netloc):
        raise HTTPException(422, "A valid web source URL is required.")
    row = get_metrics_by_company_year(company, year)
    if row is None and patch.source == "web":
        if not normalize_currency(patch.currency) or not normalize_unit(patch.unit) or patch.value is None:
            raise HTTPException(422, "A new web value requires a currency, unit and numeric value.")
        from database.save_metrics import save_metrics
        save_metrics(company, year, {}, currency=normalize_currency(patch.currency),
                     unit=normalize_unit(patch.unit), extraction_status="failed", source_type="web")
        row = get_metrics_by_company_year(company, year)
    if row is None:
        raise HTTPException(404, f"No metrics row for {company} {year}.")

    column = DB_COLUMNS[patch.metric]
    unit = normalize_unit(patch.unit) if patch.unit else None
    currency = normalize_currency(patch.currency) if patch.currency else None
    if patch.unit and not unit or patch.currency and not currency:
        raise HTTPException(422, "Invalid unit or currency.")
    if currency and row.get("currency") and currency != row["currency"]:
        raise HTTPException(422, "A single correction cannot change the currency of other metrics.")
    stored_value = patch.value
    if stored_value is not None and unit and row.get("unit"):
        stored_value *= unit_multiplier(unit) / unit_multiplier(row["unit"])
    sources = dict(row.get("metric_sources") or {})
    now_iso = datetime.now(timezone.utc).isoformat()

    if patch.reviewed and patch.value is None and not patch.quote:
        # "Mark as reviewed": an audit note, not a value override.
        notes = row.get("extraction_notes") or []
        if isinstance(notes, str):
            try:
                import json as _json
                notes = _json.loads(notes)
            except ValueError:
                notes = []
        notes = list(notes or []) + [f"Reviewed by user at {now_iso} (metric: {patch.metric})."]
        with get_engine().begin() as conn:
            conn.execute(text(
                "UPDATE financial_metrics SET extraction_status = 'ok', "
                "extraction_notes = CAST(:notes AS jsonb), updated_at = now() WHERE id = :id"
            ), {"notes": __import__("json").dumps(notes), "id": row["id"]})
        return {"success": True, "company": company, "year": year, "reviewed": True,
                "message": f"Marked {company} FY{year} as reviewed at {now_iso}."}

    entry = {
        "value": patch.value,
        "normalized_value": patch.value * unit_multiplier(unit or row.get("unit")) if patch.value is not None else None,
        "confidence": 1.0 if patch.source == "manual" else 0.7,
        "unit": unit or row.get("unit"),
        "unit_multiplier": unit_multiplier(unit or row.get("unit")),
        "currency": currency or row.get("currency"),
        "page": (sources.get(patch.metric) or {}).get("page"),
        "quote": patch.quote or (sources.get(patch.metric) or {}).get("quote"),
        "source": patch.source,
        "status": "manual_override" if patch.source == "manual" else "web_verified",
        "previous_value": (sources.get(patch.metric) or {}).get("value"),
        "document": row.get("source_file") or (sources.get(patch.metric) or {}).get("document"),
    }
    if patch.source == "web":
        entry["url"] = patch.url
        entry["retrieved_at"] = now_iso
    else:
        entry["updated_at"] = now_iso
    sources[patch.metric] = entry

    # A manual correction implies someone reviewed the row.
    status = row.get("extraction_status")
    if patch.reviewed:
        status = "ok" if all(
            (row.get(f) if f != patch.metric else patch.value) is not None for f in KPI_FIELDS
        ) else "partial"
    elif status in ("needs_review", "partial", "failed"):
        status = "ok" if all(
            (row.get(f) if f != patch.metric else patch.value) is not None for f in KPI_FIELDS
        ) else "partial"

    with get_engine().begin() as conn:
        result = conn.execute(text(f"""
            UPDATE financial_metrics
            SET {column} = :value,
                currency = COALESCE(:currency, currency),
                unit = COALESCE(:unit, unit),
                unit_multiplier = COALESCE(CAST(:multiplier AS double precision), unit_multiplier),
                metric_sources = CAST(:sources AS jsonb),
                extraction_status = :status,
                source_type = CASE WHEN :source = 'web' THEN 'web' ELSE source_type END,
                source_url = CASE WHEN :source = 'web' THEN :url ELSE source_url END,
                source_retrieved_at = CASE WHEN :source = 'web' THEN now() ELSE source_retrieved_at END,
                updated_at = now()
            WHERE id = :id
        """), {
            "value": stored_value, "currency": currency, "unit": row.get("unit") or unit,
            "id": row["id"],
            "multiplier": unit_multiplier(row.get("unit") or unit) if (row.get("unit") or unit) else None,
            "sources": __import__("json").dumps(sources),
            "status": status, "source": patch.source, "url": patch.url,
            "company": company, "year": year,
        })
    if result.rowcount == 0:
        raise HTTPException(404, f"No metrics row for {company} {year}.")
    return {"success": True, "company": company, "year": year,
            "metric": patch.metric, "value": patch.value,
            "source": patch.source, "metric_sources": {patch.metric: entry}}


@router.delete("/metrics/{company}/{year}")
def delete_report(company: str, year: int, confirm: bool = False):
    """Delete a stored report after archiving it (a real, confirmed action:
    the caller must pass ?confirm=true and the UI asks first)."""
    if not confirm:
        raise HTTPException(428, "Deletion requires explicit confirmation (?confirm=true).")
    row = get_metrics_by_company_year(company, year)
    if row is None:
        raise HTTPException(404, f"No metrics row for {company} {year}.")
    from database.save_metrics import archive_company_year

    archived = archive_company_year(company, year,
                                    reason=f"Deleted by user from the dashboard ({company} FY{year}).")
    return {"success": True, "company": company, "year": year, "archived_rows": archived,
            "message": "Report deleted; the previous row is preserved in financial_metrics_history."}


@router.post("/metrics/{company}/{year}/reextract")
def reextract(company: str, year: int):
    row = get_metrics_by_company_year(company, year)
    if row is None:
        # Try to find the row under an adjacent fiscal-year label before failing.
        from database.metrics import get_metrics_rows_for_company

        rows = get_metrics_rows_for_company(company)
        row = _normalize_year_match(rows, year)
    if row is None:
        raise HTTPException(404, f"No metrics row for {company} {year}.")

    job_id = uuid.uuid4().hex[:10]
    create_job(job_id, "reextract", row.get("source_file"), company=company, year=int(row["year"]))
    enqueue(job_id)
    return {"success": True, "job_id": job_id, "status": "queued",
            "company": company, "year": int(row["year"]),
            "message": "Re-extraction queued. Poll /api/ingest/{job_id} for progress."}


@router.get("/metrics/{company}/{year}/reextract/{job_id}")
def reextract_status(company: str, year: int, job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job.")
    return job
