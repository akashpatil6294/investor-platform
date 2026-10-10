# Phase reports

## Phase 0 — Baseline and reproduction (2026-10-07)

- Environment: Python 3.11.9; clean venv created at `C:\Users\akash\.venvs\investor-platform`
  (a venv inside the OneDrive-synced repo corrupted package metadata during install).
- `requirements.txt` was **uninstallable as written**: `pydantic==2.11.7` conflicts with
  `google-genai==2.16.0` (needs `pydantic>=2.12.5`). Fixed: `pydantic>=2.12.5,<3`.
- Test suite after the fix: `32 passed in 9.32s` (no skips).
- `docs/BASELINE.md` written with the full stage table (WORKS/BROKEN/UNVERIFIED + evidence)
  from `tmp/repro_phase0.py`.
- Reproduction of "new report shows wrong/no data": fixture PDF (Northwind Ltd, INR crore,
  FY2026-03-31) runs through real conversion/detection/extraction; deterministic extraction
  returns all six KPIs correctly, but **regex-only detection misses company and unit**
  (LLM-dependent gap), and **model outages surface as the generic "could not verify" answer**
  instead of a typed retryable error (retry ×3 happens silently first).
- Not run: live Gemini, Supabase/pgvector, Postgres, Tesseract, real browser.
