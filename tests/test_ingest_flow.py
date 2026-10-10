"""End-to-end ingestion flow tests: upload → review → confirm → extraction →
dashboard + chat — with the LLM, embedding and database boundaries mocked.

Everything between those boundaries is the real code: the upload endpoint's
validation, PDF conversion, regex identity detection, statement location,
deterministic extraction, sanity checks and the chat answer tiers.
"""

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

import database.ingest_jobs as ingest_jobs
import database.save_metrics as save_metrics_module
import routes.ingest as ingest_routes
from routes import chat
from services import chat_runtime as runtime
from services import pipeline

from tests.fixture_pdf import build_encrypted_pdf, build_northwind_pdf, build_scanned_pdf

NUMERIC_FIELDS = ("revenue", "net_income", "operating_income",
                  "cash_flow", "total_assets", "total_liabilities")


# ------------------------------------------------------------------
# Fakes for the persistent boundaries (jobs table, metrics table, LLM)
# ------------------------------------------------------------------

class FakeJobStore:
    def __init__(self):
        self.jobs = {}

    def create_job(self, job_id, kind, filename, company=None, year=None, detection=None):
        self.jobs[job_id] = {"job_id": job_id, "kind": kind, "status": "queued", "stage": None,
                             "filename": filename, "company": company, "year": year,
                             "chunks_inserted": None, "log": [], "detection": detection,
                             "result": None, "error": None}
        return dict(self.jobs[job_id])

    def update_job(self, job_id, updates=None, message=None):
        job = self.jobs.setdefault(job_id, {"job_id": job_id, "log": []})
        if message:
            job["log"].append(message)
        if updates:
            job.update(updates)

    def get_job(self, job_id):
        job = self.jobs.get(job_id)
        return dict(job) if job else None

    def count_active(self):
        return sum(1 for j in self.jobs.values()
                   if j.get("status") in ("queued", "running", "awaiting_review"))


class FakeMetricsStore:
    """Version-aware stand-in for the financial_metrics upsert path."""

    def __init__(self):
        self.rows = {}       # (company_lower, year, version) -> record
        self.history = []    # (record, reason)

    def save(self, company, year, metrics, *, duplicate_action="replace", **kw):
        present = sum(1 for f in NUMERIC_FIELDS if metrics.get(f) is not None)
        if present == 0 and (kw.get("extraction_status") or "failed") != "failed":
            raise RuntimeError("all-null row")
        key_prefix = (company.casefold(), int(year))
        existing = sorted(k for k in self.rows if (k[0], k[1]) == key_prefix)
        if existing and duplicate_action == "keep_both":
            version = existing[-1][2] + 1
        elif existing:
            for key in existing:
                record = self.rows.pop(key)
                self.history.append((record, "Replaced by a new ingestion."))
            version = 1
        else:
            version = 1
        record = {"company": company, "year": int(year), "version": version}
        for field in NUMERIC_FIELDS:
            record[field] = metrics.get(field)
        record.update(kw)
        self.rows[key_prefix + (version,)] = record
        return record

    def list_versions(self, company, year):
        return [{"company": r["company"], "year": r["year"], "version": r["version"],
                 "source_file": r.get("source_file"), "extraction_status": r.get("extraction_status"),
                 "updated_at": r.get("updated_at")}
                for (c, y, _v), r in sorted(self.rows.items())
                if c == company.casefold() and y == int(year)]

    def latest(self, company, year):
        versions = [r for (c, y, _v), r in self.rows.items()
                    if c == company.casefold() and y == int(year)]
        return versions[-1] if versions else None

    def catalog(self):
        companies = {}
        for (c, _y, _v), row in self.rows.items():
            companies.setdefault(c, []).append(row)
        result = []
        for name, rows in sorted(companies.items()):
            result.append({
                "company": rows[0]["company"],
                "years": [{
                    "year": r["year"], "version": r["version"],
                    "currency": r.get("currency"), "unit": r.get("unit"),
                    "basis": r.get("basis"),
                    "period_end": str(r.get("period_end")) if r.get("period_end") else None,
                    "extraction_status": r.get("extraction_status"),
                    "source_file": r.get("source_file"),
                    "updated_at": r.get("updated_at"),
                    "data_quality": {
                        "metrics_present": sum(1 for f in NUMERIC_FIELDS if r.get(f) is not None),
                        "metrics_total": 6,
                        "missing": [f for f in NUMERIC_FIELDS if r.get(f) is None],
                        "needs_review": r.get("extraction_status") in ("needs_review", "partial"),
                        "manual_overrides": 0,
                    },
                } for r in rows],
            })
        return result


class FakeAliasStore:
    def __init__(self):
        self.rows = {}

    def upsert(self, alias, canonical):
        self.rows[alias.casefold()] = canonical

    def resolve(self, alias):
        return self.rows.get((alias or "").casefold())


@pytest.fixture
def stores(monkeypatch):
    """Patch the three persistent boundaries; keep everything else real."""
    jobs, metrics, aliases = FakeJobStore(), FakeMetricsStore(), FakeAliasStore()

    monkeypatch.setattr(ingest_jobs, "create_job", jobs.create_job)
    monkeypatch.setattr(ingest_jobs, "update_job", jobs.update_job)
    monkeypatch.setattr(ingest_jobs, "get_job", jobs.get_job)
    monkeypatch.setattr(ingest_jobs, "count_active", jobs.count_active)
    monkeypatch.setattr(ingest_jobs, "enqueue", lambda job_id: pipeline.run_ingestion_job(job_id))

    monkeypatch.setattr(save_metrics_module, "save_metrics", metrics.save)
    monkeypatch.setattr(save_metrics_module, "list_versions", metrics.list_versions)

    import database.aliases as aliases_module

    monkeypatch.setattr(aliases_module, "upsert_alias", aliases.upsert)
    monkeypatch.setattr(aliases_module, "resolve_alias", aliases.resolve)
    monkeypatch.setattr(aliases_module, "clear_cache", lambda: None)

    # /api/companies and /api/metrics read the real SQL layer; point them at
    # the fake store (the SQL functions themselves need a live database).
    import api.main as api_main

    monkeypatch.setattr(api_main, "get_company_catalog", metrics.catalog)
    monkeypatch.setattr(api_main, "get_metrics", lambda: list(metrics.rows.values()))
    monkeypatch.setattr(api_main, "get_metrics_by_company_year",
                        lambda company, year: metrics.latest(company, year))
    return jobs, metrics, aliases


@pytest.fixture
def no_llm(monkeypatch):
    """No detection LLM, no extraction LLM: regex + deterministic paths only."""
    import ingestion.detect as detect
    import rag.kpi_extractor_v2 as v2

    monkeypatch.setattr(detect, "gemini_detection", lambda pages: None)
    monkeypatch.setattr(v2, "_try_llm", lambda *a, **k: (None, "LLM mocked off"))


@pytest.fixture
def fake_chunks(monkeypatch):
    """_store_chunks: embeddings + Supabase are not exercised here."""
    calls = []

    def fake_store_chunks(job_id, markdown_path, company, year):
        calls.append((job_id, str(markdown_path), company, year))
        return 3, "2026-03-31"

    monkeypatch.setattr(pipeline, "_store_chunks", fake_store_chunks)
    return calls


def upload(client, tmp_path, name="northwind.pdf"):
    pdf = build_northwind_pdf(tmp_path / name)
    response = client.post("/api/ingest", files={
        "file": (name, pdf.read_bytes(), "application/pdf")})
    assert response.status_code == 200, response.text
    return response.json()


# ------------------------------------------------------------------
# The end-to-end flow
# ------------------------------------------------------------------

def test_end_to_end_upload_to_chat(stores, no_llm, fake_chunks, monkeypatch, tmp_path):
    jobs, metrics, aliases = stores
    monkeypatch.setattr(chat, "get_metrics", lambda: list(metrics.rows.values()))
    monkeypatch.setattr(chat, "get_metrics_by_company_year", metrics.latest)

    client = TestClient(__import__("api.main", fromlist=["app"]).app)

    # --- upload: the pipeline runs synchronously (faked enqueue) and must
    # stop at awaiting_review with a usable regex detection.
    payload = upload(client, tmp_path)
    job_id = payload["job_id"]
    job = jobs.get_job(job_id)
    assert job["status"] == "awaiting_review", job
    detection = job["detection"]
    assert detection["company"] == "NORTHWIND LIMITED"
    assert detection["year"] == 2026 and detection["period_end"] == "2026-03-31"
    assert detection["currency"] == "INR" and detection["unit"] == "crore"
    assert detection["basis"] == "consolidated"

    # --- chat before the report is saved: must NOT answer from anywhere.
    chat._hits.clear()
    before = chat.answer(chat.ChatRequest(
        question="What was revenue in 2026?", company="NORTHWIND LIMITED", year=2026))
    assert before["answer_source"] is None

    # --- confirm with an edit + an alias; no duplicate yet.
    response = client.post(f"/api/ingest/{job_id}/confirm", json={
        "company": "Northwind Ltd", "year": 2026, "period_end": "2026-03-31",
        "currency": "INR", "unit": "crore", "basis": "consolidated",
        "aliases": ["Northwind", "NORTHWIND"]})
    assert response.status_code == 200, response.text
    assert aliases.rows.get("northwind") == "Northwind Ltd"
    assert "northwind limited" not in aliases.rows, "canonical name never aliases to itself"
    job = jobs.get_job(job_id)
    assert job["status"] in ("completed", "completed_with_warnings"), job
    assert job["company"] == "Northwind Ltd"

    row = metrics.latest("Northwind Ltd", 2026)
    assert row["revenue"] == 12400 and row["net_income"] == 1250
    assert row["cash_flow"] == 2150 and row["total_assets"] == 18300
    assert row["total_liabilities"] == 11450
    assert row["currency"] == "INR" and row["unit"] == "crore"
    sources = row["metric_sources"]
    assert sources["revenue"]["page"] == 2 and sources["revenue"]["status"] == "extracted"
    assert sources["operating_income"]["status"] == "derived"  # revenue − expenses + finance costs
    assert sources["operating_income"]["value"] == 12400 - 11010 + 310

    # --- the new company is visible on the dashboard APIs without a restart.
    catalog = client.get("/api/companies").json()
    assert any(c["company"] == "Northwind Ltd" for c in catalog["companies"])
    detail = client.get("/api/metrics/Northwind%20Ltd/2026").json()
    assert detail["success"] and detail["data"]["revenue"] == 12400
    assert detail["data"]["currency"] == "INR"

    # --- chat answers from the stored metrics, citing the stored page.
    chat._hits.clear()
    result = chat.answer(chat.ChatRequest(
        question="What was revenue in 2026?", company="Northwind Ltd", year=2026))
    assert result["answer_source"] == "Stored metrics"
    assert "12,400" in result["answer"] and "crore" in result["answer"]
    assert result["sources"] and result["sources"][0]["page"] == 2


def test_duplicate_replace_keep_both_cancel(stores, no_llm, fake_chunks, tmp_path):
    jobs, metrics, aliases = stores
    client = TestClient(__import__("api.main", fromlist=["app"]).app)

    first = upload(client, tmp_path, "northwind.pdf")
    client.post(f"/api/ingest/{first['job_id']}/confirm", json={
        "company": "Northwind Ltd", "year": 2026, "period_end": "2026-03-31",
        "currency": "INR", "unit": "crore", "basis": "consolidated"})
    assert metrics.latest("Northwind Ltd", 2026)["revenue"] == 12400

    # A second, different report for the same company/year.
    from tests.fixture_pdf import STATEMENT_ROWS
    import fitz
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_textbox(fitz.Rect(50, 60, 545, 780), (
        "NORTHWIND LIMITED\nAnnual Report 2025-26 (restated)\n"
        "All amounts are in Rs. Crore.\n"
        "For the financial year ended March 31, 2026.\n" + "context " * 30
    ), fontsize=11)
    p2 = doc.new_page()
    p2.insert_text((50, 60), "Consolidated Financial Statements", fontsize=13)
    y = 110
    for label, c1, c2 in STATEMENT_ROWS:
        p2.insert_text((50, y), label, fontsize=9)
        if c1:
            restated = c1.replace(",", "")
            if restated.isdigit():
                c1 = f"{int(restated) + 5:,}"
            p2.insert_text((330, y), c1, fontsize=9)
            p2.insert_text((440, y), c2, fontsize=9)
        y += 16
    doc.save(str(tmp_path / "northwind-restated.pdf"))
    doc.close()
    response = client.post("/api/ingest", files={
        "file": ("northwind-restated.pdf", (tmp_path / "northwind-restated.pdf").read_bytes(),
                 "application/pdf")})
    assert response.status_code == 200
    second = response.json()

    # Confirm WITHOUT a duplicate decision → 409 listing the existing row.
    conflict = client.post(f"/api/ingest/{second['job_id']}/confirm", json={
        "company": "Northwind Ltd", "year": 2026, "period_end": "2026-03-31",
        "currency": "INR", "unit": "crore", "basis": "consolidated"})
    assert conflict.status_code == 409
    body = conflict.json()
    assert body["code"] == "duplicate_report" and body["existing"][0]["version"] == 1

    # keep_both: two versions now exist; the new one is version 2.
    ok = client.post(f"/api/ingest/{second['job_id']}/confirm", json={
        "company": "Northwind Ltd", "year": 2026, "period_end": "2026-03-31",
        "currency": "INR", "unit": "crore", "basis": "consolidated",
        "duplicate_action": "keep_both"})
    assert ok.status_code == 200
    versions = metrics.list_versions("Northwind Ltd", 2026)
    assert [v["version"] for v in versions] == [1, 2]
    assert metrics.rows[("northwind ltd", 2026, 2)]["revenue"] == 12405

    # replace: archives the current rows, stores the new one as version 1.
    third = upload(client, tmp_path, "northwind-third.pdf")
    client.post(f"/api/ingest/{third['job_id']}/confirm", json={
        "company": "Northwind Ltd", "year": 2026, "period_end": "2026-03-31",
        "currency": "INR", "unit": "crore", "basis": "consolidated",
        "duplicate_action": "replace"})
    assert len(metrics.rows) == 1
    assert len(metrics.history) == 2, "both pre-replace versions must be archived"
    assert metrics.latest("Northwind Ltd", 2026)["revenue"] == 12400  # the third upload is the original fixture

    # cancel at review: nothing is stored.
    fourth = upload(client, tmp_path, "northwind-cancel.pdf")
    cancelled = client.post(f"/api/ingest/{fourth['job_id']}/confirm", json={
        "company": "Northwind Ltd", "year": 2026, "period_end": "2026-03-31",
        "currency": "INR", "unit": "crore", "basis": "consolidated",
        "duplicate_action": "cancel"})
    assert cancelled.status_code == 200 and cancelled.json().get("cancelled") is True
    assert jobs.get_job(fourth["job_id"])["status"] == "cancelled"
    assert len(metrics.rows) == 1


def test_upload_rejections(stores, tmp_path):
    client = TestClient(__import__("api.main", fromlist=["app"]).app)

    response = client.post("/api/ingest", files={
        "file": ("notes.txt", b"just text", "text/plain")})
    assert response.status_code == 400

    response = client.post("/api/ingest", files={
        "file": ("fake.pdf", b"%PDF-fake but truncated", "application/pdf")})
    assert response.status_code == 200  # magic bytes pass; the pipeline fails later with a readable error

    response = client.post("/api/ingest", files={
        "file": ("wrong.pdf", b"MZ garbage not a pdf", "application/pdf")})
    assert response.status_code == 415

    response = client.post("/api/ingest", files={
        "file": ("empty.pdf", b"", "application/pdf")})
    assert response.status_code == 400

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(ingest_routes, "MAX_UPLOAD_BYTES", 1024)
        response = client.post("/api/ingest", files={
            "file": ("big.pdf", b"%PDF-" + b"x" * 2048, "application/pdf")})
        assert response.status_code == 413
    finally:
        monkey.undo()


def test_encrypted_pdf_fails_readably(stores, no_llm, fake_chunks, monkeypatch, tmp_path):
    import services.pipeline as pipeline_module

    pdf = build_encrypted_pdf(tmp_path / "encrypted.pdf")
    monkeypatch.setenv("OCR_MODE", "off")
    with pytest.raises(RuntimeError) as excinfo:
        pipeline_module._convert_and_ocr(pdf, "job-diagnostic")
    assert "PDF conversion" in str(excinfo.value) or "no text" in str(excinfo.value).lower()


def test_scanned_pdf_without_engine_fails_clearly(stores, no_llm, fake_chunks, monkeypatch, tmp_path):
    import ingestion.ocr as ocr
    import services.pipeline as pipeline_module

    pdf = build_scanned_pdf(tmp_path / "scanned.pdf")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(ocr, "tesseract_available", lambda: False)
    with pytest.raises(RuntimeError) as excinfo:
        pipeline_module._convert_and_ocr(pdf, "job-diagnostic")
    message = str(excinfo.value)
    assert "OCR" in message or "text layer" in message


def test_running_job_can_be_cancelled_between_stages(stores, no_llm, tmp_path, monkeypatch):
    jobs, metrics, aliases = stores
    client = TestClient(__import__("api.main", fromlist=["app"]).app)

    payload = upload(client, tmp_path)
    job_id = payload["job_id"]
    assert jobs.get_job(job_id)["status"] == "awaiting_review"

    # Cancel while the (synchronous) post-confirm run is between stages: the
    # fake _store_chunks cancels the job, the next stage must refuse to run.
    def cancel_during_chunks(job_id_, path, company, year):
        ingest_jobs.update_job(job_id_, {"status": "cancelled"}, message="Cancelled by user.")
        return 3, "2026-03-31"

    monkeypatch.setattr(pipeline, "_store_chunks", cancel_during_chunks)
    client.post(f"/api/ingest/{job_id}/confirm", json={
        "company": "Northwind Ltd", "year": 2026, "period_end": "2026-03-31",
        "currency": "INR", "unit": "crore", "basis": "consolidated"})
    assert jobs.get_job(job_id)["status"] == "cancelled"
    assert metrics.rows == {}, "cancelled job must not save"


def test_job_retry_and_diagnostics(stores, no_llm, monkeypatch, tmp_path):
    jobs, metrics, aliases = stores
    client = TestClient(__import__("api.main", fromlist=["app"]).app)

    # Force a failure after review: chunk storage explodes.
    def broken_chunks(job_id, path, company, year):
        raise RuntimeError("vector store unreachable (mock)")

    monkeypatch.setattr(pipeline, "_store_chunks", broken_chunks)
    payload = upload(client, tmp_path)
    client.post(f"/api/ingest/{payload['job_id']}/confirm", json={
        "company": "Northwind Ltd", "year": 2026, "period_end": "2026-03-31",
        "currency": "INR", "unit": "crore", "basis": "consolidated"})
    failed = jobs.get_job(payload["job_id"])
    assert failed["status"] == "failed"
    assert "vector store unreachable" in (failed["error"] or "") or "Chunk" in (failed["error"] or "")

    retry = client.post(f"/api/ingest/{payload['job_id']}/retry")
    assert retry.status_code == 200 and retry.json()["status"] == "queued"

    diagnostics = client.get(f"/api/ingest/{payload['job_id']}/diagnostics")
    assert diagnostics.status_code == 200
    text = diagnostics.text
    assert payload["job_id"] in text and payload["filename"] in text
    assert "GEMINI" not in text and "sk-" not in text, "diagnostics must never leak secrets"


def test_missing_metric_reports_not_found_with_reason(no_llm, monkeypatch):
    """A report whose balance sheet is absent stores not_found entries with
    reasons instead of silently omitting the metrics."""
    from rag.kpi_extractor_v2 import build_metric_sources

    markdown_path = "whatever.md"
    entries = {"revenue": {"value": 100.0, "page": 1, "quote": "Revenue from operations|100|90",
                           "confidence": 0.9, "method": "row-label", "currency": "INR",
                           "unit": "crore"}}
    notes = ["cash_flow: not extracted — no cash flow statement found"]
    sources = build_metric_sources(entries, markdown_path, notes=notes)
    assert sources["revenue"]["status"] == "extracted"
    assert sources["total_assets"]["status"] == "not_found"
    assert "reason" in sources["total_assets"]
    assert sources["cash_flow"]["reason"] == "not extracted — no cash flow statement found"
