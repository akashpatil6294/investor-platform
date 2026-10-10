"""Upload reports and track their persistent ingestion jobs.

Uploads enqueue without a 409 (jobs run in arrival order on one worker).
The pipeline pauses at ``awaiting_review`` with the detected identity; the
client confirms or edits via ``POST /api/ingest/{job_id}/confirm``. Any
``.pdf`` filename is accepted — the filename is only a hint.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Literal

from database import ingest_jobs

REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_PDF_DIR = REPO_ROOT / "data" / "raw_pdfs"
UPLOAD_CHUNK_SIZE = 1024 * 1024
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_QUEUED_UPLOADS = 5

router = APIRouter(tags=["ingestion"])


class ReviewConfirm(BaseModel):
    company: str | None = Field(default=None, max_length=120)
    year: int | None = Field(default=None, ge=1900, le=2100)
    period_start: str | None = Field(default=None, max_length=10)
    period_end: str | None = Field(default=None, max_length=10)
    currency: str | None = Field(default=None, max_length=8)
    unit: str | None = Field(default=None, max_length=16)
    basis: str | None = Field(default=None, max_length=16)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    duplicate_action: Literal["replace", "keep_both", "cancel"] | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_filename(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._\- ]", "_", name).strip()
    return name or "report.pdf"


@router.post("/ingest")
async def upload_report(file: UploadFile = File(...)):
    """Store a validated PDF and queue its background ingestion."""
    supplied_name = (file.filename or "").replace("\\", "/")
    filename = _safe_filename(supplied_name.rsplit("/", 1)[-1])

    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only .pdf files can be ingested.")
    if ingest_jobs.count_active() >= MAX_QUEUED_UPLOADS:
        raise HTTPException(status_code=429, detail="Too many queued ingests. Wait for one to finish.")
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(415, "Upload must be a PDF.")

    handed_to_worker = False
    try:
        RAW_PDF_DIR.mkdir(parents=True, exist_ok=True)
        destination = RAW_PDF_DIR / filename
        if destination.exists():
            stem, suffix = destination.stem, destination.suffix
            filename = f"{stem}-{uuid.uuid4().hex[:4]}{suffix}"
            destination = RAW_PDF_DIR / filename

        try:
            total = 0
            with destination.open("xb") as output:
                while True:
                    chunk = await file.read(UPLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    if total == 0 and not chunk.startswith(b"%PDF-"):
                        raise HTTPException(415, "File content is not a PDF.")
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise HTTPException(413, "PDF exceeds the 25 MB limit.")
                    output.write(chunk)
                if total == 0:
                    raise HTTPException(400, "PDF is empty.")
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

        job_id = uuid.uuid4().hex[:10]
        ingest_jobs.create_job(job_id, "ingest", filename)
        ingest_jobs.enqueue(job_id)
        handed_to_worker = True

        return {
            "job_id": job_id,
            "status": "queued",
            "filename": filename,
            "message": "Queued. The pipeline will pause for identity confirmation before extracting.",
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Could not save uploaded PDF.") from exc
    finally:
        if not handed_to_worker:
            await file.close()


@router.get("/ingest/{job_id}")
def get_job(job_id: str):
    job = ingest_jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown ingestion job: {job_id}")
    job.pop("detection_internal", None)
    return job


@router.get("/ingest")
def list_jobs():
    return {"jobs": ingest_jobs.list_jobs()}


@router.post("/ingest/{job_id}/confirm")
def confirm_review(job_id: str, payload: ReviewConfirm):
    """Confirm (optionally editing) the detected identity; extraction resumes."""
    job = ingest_jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown ingestion job: {job_id}")
    if job["status"] != "awaiting_review":
        raise HTTPException(status_code=409, detail=f"Job is not awaiting review (status: {job['status']}).")
    edits = payload.model_dump(exclude_none=True)
    if "currency" in edits:
        from ingestion.units import normalize_currency

        edits["currency"] = normalize_currency(edits["currency"]) or edits["currency"].upper()
    if "unit" in edits:
        from ingestion.units import normalize_unit

        edits["unit"] = normalize_unit(edits["unit"])
    if "basis" in edits and edits["basis"] not in ("consolidated", "standalone"):
        edits.pop("basis")
    for key in ("period_start", "period_end"):
        if key in edits and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", edits[key]):
            edits.pop(key)

    from datetime import date
    detection = {**(job.get("detection") or {}), **edits}
    if not all(detection.get(k) for k in ("company", "year", "period_end", "currency", "unit", "basis")):
        raise HTTPException(422, "Confirm company, year, period end, currency, unit and basis.")
    try:
        end = date.fromisoformat(detection["period_end"])
        if end.year != int(detection["year"]):
            raise ValueError()
        if detection.get("period_start") and date.fromisoformat(detection["period_start"]) >= end:
            raise ValueError()
    except ValueError:
        raise HTTPException(422, "Use valid dates and the fiscal year in which the period ends.")
    if detection["basis"] not in ("consolidated", "standalone") or not re.fullmatch(r"[A-Z]{3}", detection["currency"]):
        raise HTTPException(422, "Choose a reporting basis and three-letter currency code.")
    detection["company"] = detection["company"].strip()
    if not detection["company"]:
        raise HTTPException(422, "Company name is required.")
    if payload.duplicate_action == "cancel":
        ingest_jobs.update_job(job_id, {"status": "cancelled", "error": "Cancelled at review."},
                               message="Cancelled at review step.")
        return {"success": True, "job_id": job_id, "cancelled": True}
    from database.save_metrics import list_versions

    existing = list_versions(detection["company"], int(detection["year"]))
    if existing and payload.duplicate_action is None:
        return JSONResponse({
            "code": "duplicate_report",
            "message": (f"{detection['company']} FY{detection['year']} is already stored. "
                        "Choose Replace, Keep both as versions, or Cancel."),
            "existing": existing,
            "retryable": False,
        }, status_code=409)
    # Alias edits from the review step: each alias points at the confirmed name.
    if payload.aliases:
        from database.aliases import upsert_alias

        for alias in payload.aliases:
            alias = (alias or "").strip()
            if alias and alias.casefold() != detection["company"].casefold():
                try:
                    upsert_alias(alias, detection["company"])
                except Exception:
                    raise HTTPException(503, "Could not save the alias table; retry once the database is reachable.")
    detection["duplicate_action"] = payload.duplicate_action or "replace"
    detection["confirmed_at"] = _now()
    ingest_jobs.update_job(job_id, {"detection": detection, "status": "queued", "stage": "confirmed"})
    ingest_jobs.enqueue(job_id)
    return {"success": True, "job_id": job_id, "applied_edits": edits}


@router.delete("/ingest/{job_id}")
def cancel_job(job_id: str):
    job = ingest_jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown ingestion job: {job_id}")
    if job["status"] not in ("queued", "awaiting_review", "running"):
        raise HTTPException(409, "Only queued, running or awaiting-review jobs can be cancelled.")
    ingest_jobs.update_job(job_id, {"status": "cancelled", "error": "Cancelled by user."},
                           message="Cancelled by user.")
    return {"success": True, "job_id": job_id}


@router.post("/ingest/{job_id}/retry")
def retry_job(job_id: str):
    """Re-queue a failed job. Queued from the stage it failed at: confirmed
    detections keep their detection and resume extraction; unconfirmed ones
    re-run detection and pause for review again."""
    job = ingest_jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown ingestion job: {job_id}")
    if job["status"] != "failed":
        raise HTTPException(409, "Only failed jobs can be retried.")
    if not (job.get("detection") or {}).get("confirmed_at"):
        ingest_jobs.update_job(job_id, {"detection": None, "stage": None},
                               message="Retrying from the start (identity was never confirmed).")
    else:
        ingest_jobs.update_job(job_id, {"stage": None},
                               message="Retrying from the confirmed identity.")
    ingest_jobs.update_job(job_id, {"status": "queued", "error": None}, message="Re-queued by user.")
    ingest_jobs.enqueue(job_id)
    return {"success": True, "job_id": job_id, "status": "queued"}


@router.get("/ingest/{job_id}/diagnostics")
def job_diagnostics(job_id: str):
    """Human-readable, secret-free failure report for a job."""
    import json as _json

    job = ingest_jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Unknown ingestion job: {job_id}")
    detection = job.get("detection") or {}
    safe_detection = {k: v for k, v in detection.items() if k != "confirmed_at"}
    lines = [
        f"Ingestion job {job_id}",
        f"kind: {job.get('kind')}", f"status: {job.get('status')}  stage: {job.get('stage')}",
        f"file: {job.get('filename')}", f"company: {job.get('company')}  year: {job.get('year')}",
        f"chunks stored: {job.get('chunks_inserted')}",
        f"error: {job.get('error') or 'none'}",
        "",
        "Detected identity (before confirmation):",
        _json.dumps(safe_detection, indent=2, default=str),
        "",
        "Stage log:",
    ]
    log = job.get("log") or []
    for entry in log:
        if isinstance(entry, dict):
            lines.append(f"  {entry.get('time', '')}  {entry.get('message', '')}")
        elif isinstance(entry, str):
            lines.append(f"  {entry}")
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse("\n".join(lines), media_type="text/plain; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="ingest-{job_id}.txt"'})
