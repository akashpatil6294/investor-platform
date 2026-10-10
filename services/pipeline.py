"""Staged PDF ingestion pipeline.

Stages: queued → converting → ocr → detecting → awaiting_review → chunking →
embedding → extracting → validating → completed | completed_with_warnings |
needs_review | failed.

The pipeline pauses at ``awaiting_review`` so the UI can confirm or correct
the detected identity (company, fiscal period, currency, unit, basis) before
extraction runs. Review is persisted and never times out automatically. Jobs are persisted (``database.ingest_jobs``) and processed by a single
worker thread in arrival order.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from rag.validation import FIELDS

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_DIR = REPO_ROOT / "data" / "markdown"
BATCH_SIZE = 50

def _progress(job_id: str, stage: str | None, message: str, status: str | None = None) -> None:
    from database import ingest_jobs

    updates = {"stage": stage}
    if status:
        updates["status"] = status
    try:
        ingest_jobs.update_job(job_id, updates, message=message)
    except Exception:
        logger.error("Could not persist progress for job %s", job_id)
    logger.info("[pipeline %s] %s", job_id, message)


class JobCancelled(Exception):
    """Raised between stages when the user cancels a running job."""


def _ensure_not_cancelled(job_id: str) -> None:
    from database import ingest_jobs

    job = ingest_jobs.get_job(job_id)
    if job is None or job["status"] in ("cancelled", "failed"):
        raise JobCancelled(job["status"] if job else "missing")


def _step(job_id: str, label: str, stage: str, operation):
    try:
        return operation()
    except Exception as exc:
        detail = str(exc).strip()
        logger.error("Ingestion step %s failed (%s): %s", label, type(exc).__name__,
                     detail[:200] or "no detail")
        suffix = f": {detail[:200]}" if detail else f" ({type(exc).__name__})."
        raise RuntimeError(f"{label} failed{suffix}") from None


def _filename_identity(filename: str) -> tuple[str | None, int | None]:
    from services.reports import identity

    try:
        company, year = identity(filename)
        return company, int(year)
    except Exception:
        return None, None


def _convert_and_ocr(pdf_path: Path, job_id: str) -> str:
    from ingestion.ocr import gemini_page_ocr, needs_ocr, ocr_pdf_to_pages, ocr_page
    from ingestion.pdf_to_markdown import PDFToMarkdownConverter, split_markdown_pages

    _progress(job_id, "converting", "Converting PDF to markdown (page-aware)...")
    converter = PDFToMarkdownConverter()
    markdown_path = Path(_step(
        job_id, "PDF conversion", "converting",
        lambda: converter.convert_pdf(str(pdf_path), str(MARKDOWN_DIR),
            ocr_fn=ocr_page if os.getenv("OCR_MODE", "auto") != "off" else None,
            progress=lambda msg: _progress(job_id, "ocr" if msg.startswith("OCR") else "converting", msg)),
    ))
    markdown = markdown_path.read_text(encoding="utf-8", errors="ignore")

    import fitz

    try:
        with fitz.open(str(pdf_path)) as doc:
            page_count = doc.page_count
    except Exception:
        page_count = 0

    from ingestion.ocr import text_density

    if needs_ocr(markdown, page_count):
        _progress(job_id, "ocr",
                  f"Low text density ({text_density(markdown, page_count):.0f} chars/page) — "
                  "running OCR (Tesseract if installed, else Gemini vision)...")
        pages = _step(job_id, "OCR", "ocr", lambda: ocr_pdf_to_pages(
            str(pdf_path),
            progress=lambda msg: _progress(job_id, "ocr", msg),
        ))
        # OCR paths for per-page conversion: re-run per-page with OCR callback
        # is expensive; assemble from OCR'd pages directly.
        markdown_content = "\n\n".join(
            f"<!-- PAGE:{number} -->\n\n{text.strip()}" for number, text in pages
        )
        markdown_path.write_text(markdown_content, encoding="utf-8")
        markdown = markdown_content
        _progress(job_id, "ocr", f"OCR complete: {len(pages)} pages transcribed.")
    else:
        pages = _step(job_id, "Page split", "converting", lambda: split_markdown_pages(markdown))
        if len(pages) <= 1 and page_count > 1:
            # Legacy-style flat conversion lost page numbers; rebuild them.
            _progress(job_id, "converting",
                      "No page markers found; re-converting page by page to keep page numbers...")
            rebuilt = _step(job_id, "Page-aware re-conversion", "converting",
                            lambda: converter.convert_pages(str(pdf_path)))
            markdown_content = "\n\n".join(
                f"<!-- PAGE:{number} -->\n\n{text.strip()}" for number, text in rebuilt
            )
            markdown_path.write_text(markdown_content, encoding="utf-8")
            markdown = markdown_content
            pages = rebuilt

    if not markdown.strip():
        raise RuntimeError(
            "The PDF produced no text (likely scanned images) and OCR produced nothing.")
    _progress(job_id, "converting", f"Markdown ready: {len(markdown):,} characters, {len(pages)} pages.")
    return markdown


def _detect(job_id: str, markdown: str, pdf_name: str) -> dict:
    from ingestion.detect import detect_report_identity, summarize_detection

    _progress(job_id, "detecting", "Detecting company, fiscal period, currency and unit...")
    detection = _step(
        job_id, "Report detection", "detecting",
        lambda: detect_report_identity(markdown, filename=pdf_name),
    )
    _progress(job_id, "detecting", f"Detected: {summarize_detection(detection)}")
    return detection


def _store_chunks(job_id: str, markdown_path: Path, company: str, year: int | None) -> tuple[int, str | None]:
    import json
    from sqlalchemy import text
    from database.postgres_sql import get_engine
    from ingestion.pdf_to_markdown import split_markdown_pages
    from ingestion.semantic_chunker import LocalEmbeddings, get_text_splitter
    from ingestion.units import infer_period_end

    markdown = markdown_path.read_text(encoding="utf-8", errors="ignore")
    page_map = _step(job_id, "Page split", "chunking", lambda: split_markdown_pages(markdown))
    _progress(job_id, "chunking", f"Chunking {len(page_map)} pages with page metadata...")
    chunks = _step(job_id, "Markdown chunking", "chunking", lambda: _chunk_pages(page_map, get_text_splitter()))
    total = len(chunks)

    _progress(job_id, "embedding", f"Generating embeddings for {total} chunks...")
    embedding_model = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
    embeddings = _step(job_id, "Embedding model setup", "embedding",
                       lambda: LocalEmbeddings(model_name=embedding_model))
    vectors = _step(job_id, "Embedding generation", "embedding",
                    lambda: embeddings.embed_documents([c.page_content for c, _ in chunks]))
    if len(vectors) != len(chunks):
        raise RuntimeError(f"Embedding count mismatch: {len(vectors)} vectors for {len(chunks)} chunks.")

    period_end = infer_period_end(markdown[:20000], year)
    # Replace one document atomically, only after every embedding succeeds.
    table = os.getenv("VECTOR_TABLE_NAME", "documents")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
        raise ValueError("Invalid vector table name")
    records = []
    for index, ((chunk, page), vector) in enumerate(zip(chunks, vectors)):
        records.append(dict(content=chunk.page_content,
            metadata=json.dumps(dict(source=markdown_path.name, chunk_index=index,
                                     company=company, year=str(year), page=page)),
            embedding=json.dumps(vector), company=company, year=str(year),
            source_file=markdown_path.name))
    with get_engine().begin() as conn:
        conn.execute(text(f'DELETE FROM "{table}" WHERE source_file=:source'),
                     {"source": markdown_path.name})
        for start in range(0, total, BATCH_SIZE):
            conn.execute(text(f'''INSERT INTO "{table}"
                (content, metadata, embedding, company, year, source_file)
                VALUES (:content, CAST(:metadata AS jsonb), CAST(:embedding AS vector),
                        :company, :year, :source_file)'''), records[start:start+BATCH_SIZE])
    _progress(job_id, "embedding", f"Stored {total} chunks with physical page numbers.")
    return total, period_end.isoformat() if period_end else None


def _chunk_pages(page_map, splitter):
    """Chunk each page separately so every chunk carries its real page."""
    from langchain_core.documents import Document

    chunked: list[tuple[Document, int]] = []
    for number, text in page_map:
        if not text.strip():
            continue
        for piece in splitter.split_text(text):
            chunked.append((Document(page_content=piece), number))
    return chunked


def _extract_and_validate(job_id: str, company: str, year: int | None, detection: dict,
                          source_file: str, progress_prefix: str = "") -> dict:
    from rag.kpi_extractor_v2 import build_metric_sources, extract_metrics_from_report
    from rag.validation import run_checks

    def progress(message: str) -> None:
        _progress(job_id, "extracting", f"{progress_prefix}{message}")

    result = _step(job_id, "KPI extraction", "extracting", lambda: extract_metrics_from_report(
        company=company, year=year, source_file=source_file,
        detection=detection, progress=progress,
    ))
    sources = build_metric_sources(result["metric_entries"], source_file,
                                   notes=result.get("notes"))
    return {**result, "metric_sources": sources}


def _save(job_id: str, company: str, year: int | None, detection: dict, extraction: dict,
          source_file: str) -> dict:
    from database.save_metrics import save_metrics

    metrics = {k: v for k, v in extraction["metrics"].items()}
    record = _step(job_id, "Saving metrics", "validating", lambda: save_metrics(
        company=company,
        year=year or detection.get("year") or 0,
        metrics=metrics,
        currency=detection.get("currency"),
        unit=detection.get("unit"),
        period_start=detection.get("period_start"),
        period_end=detection.get("period_end"),
        basis=detection.get("basis"),
        extraction_status=extraction["status"],
        extraction_notes=extraction["notes"],
        metric_sources=extraction["metric_sources"],
        source_file=source_file,
        qualitative=extraction.get("qualitative"),
        source_type="pipeline",
        duplicate_action=detection.get("duplicate_action") or "replace",
    ))
    return record


def _refresh_visible_state() -> None:
    """A finished job must be visible to chat and the dashboard immediately:
    drop the alias cache (new company names may have been added)."""
    try:
        from database.aliases import clear_cache

        clear_cache()
    except Exception:
        logger.warning("Could not refresh the company alias cache.")


def run_ingestion_job(job_id: str) -> None:
    """Worker entry point: read the queued job and run all stages."""
    from database import ingest_jobs

    job = ingest_jobs.get_job(job_id)
    if job is None or job["status"] != "queued":
        return
    pdf_path = REPO_ROOT / "data" / "raw_pdfs" / job["filename"]
    try:
        _ensure_not_cancelled(job_id)
        ingest_jobs.update_job(job_id, {"status": "running", "stage": "converting"},
                               message="Starting ingestion.")
        detection = job.get("detection") or {}
        if not detection.get("confirmed_at"):
            markdown = _convert_and_ocr(pdf_path, job_id)
            _ensure_not_cancelled(job_id)
            detection = _detect(job_id, markdown, pdf_path.name)
            ingest_jobs.update_job(job_id, {"status": "awaiting_review",
                "stage": "awaiting_review", "detection": detection},
                message="Confirm company, fiscal period, currency, unit and basis to continue.")
            return
        company = detection.get("company") or _filename_identity(pdf_path.name)[0] or pdf_path.stem
        year = detection.get("year")
        ingest_jobs.update_job(job_id, {"company": company, "year": year,
                                        "detection": detection})

        total, _pe = _step(job_id, "Vector store update", "embedding",
                           lambda: _store_chunks(job_id, MARKDOWN_DIR / f"{pdf_path.stem}.md",
                                                 company, year))
        _ensure_not_cancelled(job_id)

        extraction = _extract_and_validate(
            job_id, company, year, detection, f"{pdf_path.stem}.md")
        _ensure_not_cancelled(job_id)

        # Never save an all-null row as success; keep chunks for chat.
        numeric_present = sum(
            1 for k in FIELDS if extraction["metrics"].get(k) is not None)
        status = extraction["status"]
        if numeric_present == 0:
            _save(job_id, company, year, detection, extraction, f"{pdf_path.stem}.md")
            ingest_jobs.update_job(job_id, {
                "status": "failed", "stage": "validating",
                "error": ("Extraction failed: no financial KPIs could be located in the "
                          "document. The document remains searchable in chat; try "
                          "re-extract or review the source file."),
                "chunks_inserted": total,
                "result": {"company": company, "year": year, "extraction_status": status,
                           "notes": extraction["notes"]},
            })
            return

        _save(job_id, company, year, detection, extraction, f"{pdf_path.stem}.md")

        missing = [k for k in FIELDS if extraction["metrics"].get(k) is None]
        if status == "failed":
            final_status = "failed"
        elif status == "needs_review":
            final_status = "needs_review"
        elif missing or extraction["failures"]:
            final_status = "completed_with_warnings"
        else:
            final_status = "completed"
        summary = (f"{company} FY{year} added — {numeric_present} of "
                   f"{len(FIELDS)} KPIs extracted"
                   + (f", {len(missing)} not found" if missing else "")
                   + (f", {len(extraction['failures'])} failed sanity checks" if extraction["failures"] else "")
                   + ".")
        ingest_jobs.update_job(job_id, {
            "status": final_status, "stage": "completed",
            "chunks_inserted": total, "error": None,
            "result": {"company": company, "year": year, "extraction_status": status,
                       "summary": summary, "notes": extraction["notes"],
                       "failures": extraction["failures"], "missing": missing},
        }, message=summary)
    except JobCancelled:
        logger.info("Ingestion job %s was cancelled between stages.", job_id)
    except Exception as exc:
        logger.error("Ingestion job %s failed (%s)", job_id, type(exc).__name__)
        # RuntimeErrors from _step carry a curated, secret-free message that
        # is worth showing; anything else stays generic.
        message = None
        if isinstance(exc, RuntimeError) and " failed" in str(exc):
            message = str(exc)
        ingest_jobs.update_job(
            job_id,
            {"status": "failed",
             "error": message or f"Ingestion failed ({type(exc).__name__}); review the report and retry."},
        )
    finally:
        _refresh_visible_state()


def run_reextraction_job(job_id: str, company: str, year: int) -> None:
    """Re-run detection + extraction + validation against the stored markdown."""
    from database import ingest_jobs
    from database.metrics import get_metrics_by_company_year

    job = ingest_jobs.get_job(job_id)
    if job is None or job["status"] != "queued":
        return
    try:
        ingest_jobs.update_job(job_id, {"status": "running", "stage": "detecting"})
        row = get_metrics_by_company_year(company, year)
        source_file = (row or {}).get("source_file") or f"{year}_{company}.md"
        markdown_path = MARKDOWN_DIR / source_file
        if not markdown_path.is_file():
            raise RuntimeError(f"Source markdown not found for {company} {year}: {source_file}")
        markdown = markdown_path.read_text(encoding="utf-8", errors="ignore")

        from ingestion.detect import detect_report_identity

        detection = _detect(job_id, markdown, source_file)
        # Keep the stored identity unless detection is confident about a change.
        for key in ("period_end", "currency", "unit", "basis"):
            if row and row.get(key):
                detection[key] = str(row.get(key))
        if row and row.get("year") and not detection.get("year"):
            detection["year"] = row["year"]

        extraction = _extract_and_validate(
            job_id, company, year, detection, source_file)
        _save(job_id, company, year, detection, extraction, source_file)
        numeric_present = sum(1 for k in FIELDS if extraction["metrics"].get(k) is not None)
        summary = (f"Re-extracted {company} FY{year} — {numeric_present} of "
                   f"{len(FIELDS)} KPIs.")
        ingest_jobs.update_job(job_id, {
            "status": "needs_review" if extraction["status"] == "needs_review"
                      else ("failed" if extraction["status"] == "failed" else
                            "completed_with_warnings" if extraction["status"] == "partial" else "completed"),
            "stage": "completed", "error": None,
            "result": {"company": company, "year": year,
                       "extraction_status": extraction["status"], "summary": summary,
                       "notes": extraction["notes"]},
        }, message=summary)
    except JobCancelled:
        logger.info("Re-extraction job %s was cancelled between stages.", job_id)
    except Exception as exc:
        logger.error("Re-extraction job %s failed (%s)", job_id, type(exc).__name__)
        ingest_jobs.update_job(job_id, {"status": "failed", "error": f"Re-extraction failed ({type(exc).__name__})."})
    finally:
        _refresh_visible_state()


def run_ingestion(pdf_path: Path, progress_cb=None) -> dict:
    """Scripts use the same mandatory review checkpoint as HTTP uploads."""
    from database import ingest_jobs
    import uuid
    job_id = uuid.uuid4().hex[:10]
    ingest_jobs.create_job(job_id, "ingest", Path(pdf_path).name)
    run_ingestion_job(job_id)
    return ingest_jobs.get_job(job_id)
