# Arbitrary-report implementation evidence

## Phase 0
- `2024_Flipkart.md` is absent; `2025_Flipkart.md` has 748,786 characters.
- Exact company chunk count: 1,440, all indexed under FY2025.
- FY2025 stored row is partial, not all-null: revenue 204933, net/operating income both -14640, assets 130626, liabilities 118804; cash flow null (INR million).
- Direct legacy FY2024 extraction returned revenue 179073, net/operating income both -23283, assets 106045, liabilities 111669, cash flow null. Sanitized query/chunk and JSON output is in flipkart-legacy-diagnostic.txt.
- Confirmed legacy USD/US-product prompt assumptions and unreliable operating-income substitution.
- This report has substantial native text; OCR is not the cause here. FY2024 is a comparative period in the FY2025 document.
- Existing uncommitted rewrite already adds persistent jobs/metadata, but review timed out automatically, re-extraction dispatched incorrectly, and tuple chunk embedding was broken.
- Proceeding with durable mandatory review, statement evidence and exact fiscal-year scope; existing work is preserved.
