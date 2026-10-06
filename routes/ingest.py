"""Upload reports and track their background ingestion jobs."""

import copy
import re
import threading
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[1]
RAW_PDF_DIR = REPO_ROOT / "data" / "raw_pdfs"
PDF_FILENAME_RE = re.compile(r"^\d{4}_[A-Za-z0-9\-_]+\.pdf$", re.IGNORECASE)
UPLOAD_CHUNK_SIZE = 1024 * 1024
MAX_UPLOAD_BYTES = 25 * 1024 * 1024

router = APIRouter(tags=["ingestion"])
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_ingest_lock = threading.Lock()


class JobStatus(BaseModel):
    job_id: str
    status: str
    filename: str
    log: list[str] = Field(default_factory=list)
    company: str | None = None
    year: int | None = None
    chunks_inserted: int | None = None
    error: str | None = None
    created_at: str
    updated_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _update_job(job_id: str, updates: dict | None = None, message: str | None = None):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        if updates:
            job.update(updates)
        if message:
            job["log"].append(message)
        job["updated_at"] = _now()
        return copy.deepcopy(job)


def _run_pipeline(job_id: str, pdf_path: Path) -> None:
    """Run one ingestion job and release the single-job gate on completion."""
    try:
        _update_job(
            job_id,
            {"status": "running"},
            f"Starting ingestion for {pdf_path.name}",
        )

        try:
            # Import only in the worker, so importing routes.ingest remains
            # lightweight during API startup.
            from services.pipeline import run_ingestion

            result = run_ingestion(
                pdf_path=pdf_path,
                progress_cb=lambda message: _update_job(job_id, message=message),
            )
            _update_job(
                job_id,
                {
                    "status": "completed",
                    "company": result.get("company"),
                    "year": result.get("year"),
                    "chunks_inserted": result.get("chunks_inserted"),
                    "error": None,
                },
            )
        except Exception as exc:
            print(f"[ingest] job {job_id} failed ({type(exc).__name__})")
            _update_job(
                job_id,
                {"status": "failed", "error": "Ingestion failed. Check server configuration and retry."},
            )
    finally:
        _ingest_lock.release()


@router.post("/ingest")
async def upload_report(file: UploadFile = File(...)):
    """Store a validated PDF and queue its background ingestion."""
    supplied_name = (file.filename or "").replace("\\", "/")
    filename = supplied_name.rsplit("/", 1)[-1]

    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only .pdf files can be ingested.")
    if not PDF_FILENAME_RE.fullmatch(filename):
        raise HTTPException(
            status_code=400,
            detail=(
                "Filename must match YYYY_CompanyName.pdf and use only letters, "
                "numbers, hyphens, or underscores in the company name "
                "(for example, 2024_NVIDIA.pdf)."
            ),
        )
    if not _ingest_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Another ingestion is in progress. Please wait.",
        )

    handed_to_worker = False
    try:
        RAW_PDF_DIR.mkdir(parents=True, exist_ok=True)
        destination = RAW_PDF_DIR / filename
        if destination.exists():
            raise HTTPException(409, 'This report filename already exists. Use a different fiscal-year report.')
        if file.content_type not in ('application/pdf', 'application/octet-stream'):
            raise HTTPException(415, 'Upload must be a PDF.')

        try:
            total = 0
            with destination.open("xb") as output:
                while True:
                    chunk = await file.read(UPLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    if total == 0 and not chunk.startswith(b'%PDF-'):
                        raise HTTPException(415, 'File content is not a PDF.')
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise HTTPException(413, 'PDF exceeds the 25 MB limit.')
                    output.write(chunk)
                if total == 0:
                    raise HTTPException(400, 'PDF is empty.')
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

        job_id = uuid.uuid4().hex[:10]
        timestamp = _now()
        job = {
            "job_id": job_id,
            "status": "queued",
            "filename": filename,
            "log": [f"Queued {filename}"],
            "company": None,
            "year": None,
            "chunks_inserted": None,
            "error": None,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        with _jobs_lock:
            _jobs[job_id] = job

        thread = threading.Thread(
            target=_run_pipeline,
            args=(job_id, destination),
            daemon=True,
            name=f"ingest-{job_id}",
        )
        try:
            # Mark ownership before start so a fast worker cannot release the
            # lock before the request's cleanup path runs.
            handed_to_worker = True
            thread.start()
        except Exception:
            handed_to_worker = False
            with _jobs_lock:
                _jobs.pop(job_id, None)
            raise

        return {"job_id": job_id, "status": "queued", "filename": filename}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="Could not save uploaded PDF.",
        ) from exc
    finally:
        if not handed_to_worker:
            _ingest_lock.release()


@router.get("/ingest/{job_id}", response_model=JobStatus)
def get_job(job_id: str):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Unknown ingestion job: {job_id}")
        return copy.deepcopy(job)


@router.get("/ingest")
def list_jobs():
    with _jobs_lock:
        jobs = copy.deepcopy(list(_jobs.values()))
    jobs.sort(key=lambda job: job["created_at"], reverse=True)
    return {"jobs": jobs}

