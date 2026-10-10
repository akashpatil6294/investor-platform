# Legacy extraction path (quarantined 2026-10-07)

These modules are **not** part of the serving pipeline. The ingestion pipeline
(`services/pipeline.py` → `rag/kpi_extractor_v2.py`) is the only extraction
path; it is jurisdiction-neutral (currency/unit/period come from detection +
the review step) and never substitutes net income for operating income.

Why these were quarantined (evidence in `docs/root-cause.md` and
`docs/flipkart-phase-report.md`):

- `kpi_extractor_rag.py` — legacy prompts assumed USD, millions and US
  companies; extraction for Flipkart (INR crore, FY2025) was wrong, and
  operating income was silently substituted by net income. It also pulls in
  Groq as a primary provider.
- `extract_all_kpis.py` — batch driver around the legacy extractor.
- `refresh_report_evidence.py` — Groq-based evidence refresh; superseded by
  the v2 extraction + validation path.

Do not import anything here from `routes/`, `services/`, or `ingestion/`.
They are kept only as historical reference.
