# Baseline — Phase 0 (2026-10-07)

Environment: Python 3.11.9, clean venv at `C:\Users\akash\.venvs\investor-platform`
(created outside the OneDrive-synced repo folder; a venv created inside the repo
corrupted its own package metadata during pip install — OneDrive sync race).

## Environment findings

| Item | Result | Evidence |
|---|---|---|
| `pip install -r requirements.txt` (as found) | **FAILED — uninstallable** | `pydantic==2.11.7` pinned, but `google-genai==2.16.0` requires `pydantic>=2.12.5`. Pip: `ResolutionImpossible`. Fixed by relaxing to `pydantic>=2.12.5,<3` (only change to requirements.txt). |
| `pip check` (after fix) | clean | `No broken requirements found.` |
| `python -m pytest -q` (after fix) | **32 passed in 9.32s** | full quoted output below |

```
$ .venv/Scripts/python -m pytest -q
................................                                         [100%]
32 passed in 9.32s
```

Heavy dependencies (`sentence-transformers`, `supabase`, `pymupdf4llm`) installed
without error in the clean venv. No test was skipped.

## Ingestion stage table

Reproduction script: `tmp/repro_phase0.py` (fixture: "Northwind Ltd", INR,
FY ending 2026-03-31, 2-page PDF with statements laid out as an aligned grid).
All LLM/network/DB calls mocked or absent; every "RUN" status below is from that
script's actual output, quoted in `Evidence`.

| Stage | Status | Evidence |
|---|---|---|
| upload (`POST /api/ingest`) | UNVERIFIED (DB-bound) | Code validates magic bytes/25 MB/429 on >5 active jobs (routes/ingest.py:49-109) and is unit-tested for review-persistence, but the endpoint writes `ingest_jobs` via Postgres — not exercised against a live DB. |
| detect | **WORKS, 2 gaps** | Regex-only (no LLM) on the fixture: `year=2026, period_end=2026-03-31, currency=INR, basis=consolidated, confidence 0.4` — but `company=None` and `unit=None` (script A3). The cover says "NORTHWIND LIMITED" and "All amounts are in Rs. Crore": the top-line regex misses it because pymupdf4llm merged the cover block into one paragraph (no own line), and `infer_unit_from_text` has no pattern for "in Rs. Crore". With a `GEMINI_API_KEY` present the LLM pass would repair both; without one the review form starts empty and confirm 422s until the user types everything. |
| review (`awaiting_review`) | WORKS (code+tests) | `test_persistent_review_does_not_extract` proves no extraction before confirm; confirm endpoint validates fiscal-year rule (period end year == report year, routes/ingest.py:154-160). Live UI flow not exercised. |
| OCR | UNVERIFIED | No OCR engine installed in this environment; code path exists (`ingestion/ocr.py`, Tesseract → Gemini vision fallback) and `test_text_and_scanned_pdf` proves the per-page OCR callback fires for low-text pages. Real Tesseract: not run. |
| markdown conversion | WORKS | Script A2: `pages=2, pipe chars=64, chars=1362, page markers=yes`. |
| chunk + embed | WORKS (code) / NOT RUN (model) | `_chunk_pages` splits per page keeping real page numbers (pipeline.py:181-191); embedding requires the `sentence-transformers/all-MiniLM-L6-v2` download + Supabase/pgvector write — not run. |
| extract | **WORKS (deterministic pass)** | Script A5 on the new fixture, `use_llm=False`: `revenue=12400, net_income=1250, operating_income=1700 (derived: revenue − total expenses + finance costs), cash_flow=2150, total_assets=18300, total_liabilities=11450`. LLM cross-check pass: not run (no Gemini). |
| validate | WORKS | Script A6: `failures=none (assets = liabilities + equity holds)`; unit tests cover identity/magnitude/duplicate-value checks. |
| save (`save_metrics`) | NOT RUN (DB-bound) | Refuses all-null rows as success (raises); `ON CONFLICT (company, year)` upsert requires live Postgres. |
| dashboard API | UNVERIFIED (DB-bound) | `/api/metrics`, `/api/metrics/{company}/{year}` verified only through mocked tests; no live Postgres here. |
| chat | WORKS with mocks / NOT RUN (live Gemini) | All three tiers + honest failure proven in `tests/test_report_chat.py` and repro part B below. |

## Chat failure-path reproduction (script part B)

Model mocked to fail; retriever returns valid sources; web research stubbed
unavailable (no network). What the user actually receives:

| Model behaviour | Retry attempts observed | What the user sees |
|---|---|---|
| 503 | 3 | HTTP 200, `answer_source: null`, answer = "I could not verify an answer for this exact company and fiscal period. Checked tiers: Stored metrics: not found; Report: unavailable; Web: unavailable. Review the uploaded report or confirm the company and fiscal year." |
| 429 | 3 | same as 503 |
| 500 | 3 | same as 503 |
| timeout (stream raises `TimeoutError`) | 3 | same as 503 |
| empty answer (stream yields nothing) | 3 | same as 503 |
| request-level timeout (`wait_for` aborts) | — | HTTP 504 `{"code":"timeout","message":"The assistant took too long. Please retry.","retryable":true}` |

**Findings that drive Phase 1/2 work:**

1. Retry with backoff for 429/5xx exists (3 attempts per model), but after the
   last attempt a model outage is **indistinguishable from "no evidence"**: the
   `llm_unavailable` typed error is swallowed by the Report-tier `except` and
   rewritten as the tier-4 "could not verify" sentence. The UI shows no specific
   "model busy" message and no Retry badge. Phase 2 must surface typed errors.
2. Regex-only detection misses company name and unit on a plain two-page report
   (see detect row). Detection must be improved generically, and the review form
   must be pre-fillable without an LLM.
3. `on conflict (company, year)` has no visible migration creating that unique
   constraint (`database/create_tables.py` doesn't; migrations.py doesn't) —
   flagged for Phase 1 duplicate handling.
4. The `documents` vector table and `match_documents` RPC are created outside
   this repo (Supabase-side); chunk writes go through `VECTOR_TABLE_NAME`
   (default `documents`) — noted, not broken.

## "Not run" list (no live credentials/services exercised)

Real Gemini calls (chat, detection LLM, extraction LLM, web research), live
Supabase/pgvector retrieval, live Postgres, a real OCR engine (Tesseract), a
real browser session, very large PDFs (>25 MB rejected at upload; no 100+ page
ingestion attempted in this environment).
