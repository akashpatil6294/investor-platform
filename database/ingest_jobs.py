"""Persistent ingestion jobs.

Jobs live in the ``ingest_jobs`` table so a restart does not lose them, and a
queue allows several uploads without rejecting the second one with a 409.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

from sqlalchemy import text

from database.postgres_sql import get_engine

MAX_QUEUED = 5
# A single job must not hold the one worker forever; on timeout the job is
# marked failed with a reason and the worker moves on.
JOB_TIMEOUT_SECONDS = int(os.getenv("INGEST_TIMEOUT_SECONDS", "1800"))

# Process-local queue feeding the single worker thread.
_queue: "queue.Queue[str]" = None
_worker_started = False
_queue_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job(job_id: str, kind: str, filename: str | None, company=None, year=None,
               detection: dict | None = None) -> dict:
    engine = get_engine()
    job = {
        "job_id": job_id, "kind": kind, "status": "queued", "stage": None,
        "filename": filename, "company": company, "year": year,
        "chunks_inserted": None, "log": [], "detection": detection,
        "result": None, "error": None,
    }
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO ingest_jobs (job_id, kind, status, filename, company, year, detection, log, created_at, updated_at)
            VALUES (:job_id, :kind, 'queued', :filename, :company, :year,
                    CAST(:detection AS jsonb), CAST(:log AS jsonb), now(), now())
        """), {
            "job_id": job_id, "kind": kind, "filename": filename,
            "company": company, "year": year,
            "detection": json.dumps(detection) if detection else None,
            "log": json.dumps([f"Queued {filename or job_id}"]),
        })
    return job


def update_job(job_id: str, updates: dict | None = None, message: str | None = None) -> None:
    engine = get_engine()
    sets = ["updated_at = now()"]
    params: dict = {"job_id": job_id}
    if message:
        sets.append("log = log || CAST(:message AS jsonb)")
        params["message"] = json.dumps([{"time": _now(), "message": message}])
    if updates:
        allowed = ("status", "stage", "company", "year", "chunks_inserted", "error", "detection", "result")
        for key in allowed:
            if key in updates:
                value = updates[key]
                if key in ("detection", "result"):
                    sets.append(f"{key} = CAST(:{key} AS jsonb)")
                    value = json.dumps(value)
                else:
                    sets.append(f"{key} = :{key}")
                params[key] = value
    with engine.begin() as conn:
        conn.execute(text(f"UPDATE ingest_jobs SET {', '.join(sets)} WHERE job_id = :job_id"), params)


def get_job(job_id: str) -> dict | None:
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT * FROM ingest_jobs WHERE job_id = :job_id"
        ), {"job_id": job_id}).mappings().first()
    if row is None:
        return None
    return _normalize(row)


def list_jobs(limit: int = 20) -> list[dict]:
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT * FROM ingest_jobs ORDER BY created_at DESC LIMIT :limit"
        ), {"limit": limit}).mappings().all()
    return [_normalize(row) for row in rows]


def _normalize(row) -> dict:
    job = dict(row)
    for key in ("log", "detection", "result"):
        if isinstance(job.get(key), str):
            try:
                job[key] = json.loads(job[key])
            except ValueError:
                job[key] = None
    for key in ("created_at", "updated_at"):
        if isinstance(job.get(key), datetime):
            job[key] = job[key].isoformat()
    return job


def recover_interrupted() -> int:
    """Preserve reviews and final results; resume queued work on this single-worker server."""
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE ingest_jobs SET status='queued', updated_at=now() WHERE status='running'"))
        waiting = conn.execute(text("SELECT job_id FROM ingest_jobs WHERE status='queued' ORDER BY created_at")).scalars().all()
    for job_id in waiting:
        enqueue(job_id)
    return len(waiting)


def count_active() -> int:
    engine = get_engine()
    with engine.connect() as conn:
        n = conn.execute(text(
            "SELECT count(*) FROM ingest_jobs WHERE status IN ('queued','running','awaiting_review')"
        )).scalar()
    return int(n or 0)


def enqueue(job_id: str) -> None:
    global _queue, _worker_started
    with _queue_lock:
        if _queue is None:
            import queue as _queue_module

            _queue = _queue_module.Queue()
        _queue.put(job_id)
        if not _worker_started:
            _worker_started = True
            thread = threading.Thread(target=_worker_loop, daemon=True, name="ingest-worker")
            thread.start()


def _worker_loop() -> None:
    while True:
        job_id = _queue.get()
        try:
            from services.pipeline import run_ingestion_job, run_reextraction_job
            job = get_job(job_id)
            if job and job["status"] == "queued":
                if job["kind"] == "reextract":
                    _run_with_timeout(run_reextraction_job, job_id, job["company"], job["year"])
                else:
                    _run_with_timeout(run_ingestion_job, job_id)
        except Exception:
            import logging

            logging.getLogger(__name__).error("Ingestion worker crashed for job %s", job_id)
            try:
                update_job(job_id, {"status": "failed", "error": "Internal ingestion error."})
            except Exception:
                pass


def _run_with_timeout(fn, job_id: str, *args) -> None:
    """Run one job on its own thread; mark it failed when it exceeds the limit."""
    failure: dict = {}

    def target():
        try:
            fn(job_id, *args)
        except Exception as exc:  # surfaced in the joiner below
            failure["error"] = exc

    thread = threading.Thread(target=target, name=f"job-{job_id}", daemon=True)
    thread.start()
    thread.join(JOB_TIMEOUT_SECONDS)
    if thread.is_alive():
        update_job(job_id, {
            "status": "failed",
            "error": (f"Ingestion exceeded the {JOB_TIMEOUT_SECONDS}s job limit and was "
                      "stopped. Try splitting the PDF or raising INGEST_TIMEOUT_SECONDS."),
        }, message=f"Job timed out after {JOB_TIMEOUT_SECONDS}s.")
        return
    if failure:
        raise failure["error"]
