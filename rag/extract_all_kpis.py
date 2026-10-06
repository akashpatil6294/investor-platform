"""
Batch KPI extraction for every (company, year) in Supabase.
Reuses rag.kpi_extractor_rag's existing pipeline.

Run from REPO ROOT:
    python -m rag.extract_all_kpis
"""

import os
import time

from dotenv import load_dotenv
from supabase import create_client

from rag.kpi_extractor_rag import (
    SentenceTransformerEmbeddings,
    Retriever,
    extract_financial_metrics,
    EMBEDDING_MODEL,
)
from vectorstore.supabase_vectorstore import SupabaseVectorStore
from database.save_metrics import save_metrics

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY")
TABLE_NAME = os.getenv("VECTOR_TABLE_NAME", "documents")

supabase = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)

# Groq free tier = 8,000 tokens per minute per model.
# A single extraction uses ~7,000 tokens.
# Wait 60s between companies so the TPM window resets.
SECONDS_BETWEEN_COMPANIES = int(
    os.getenv("SECONDS_BETWEEN_COMPANIES", "60")
)


def discover_targets() -> list[tuple[str, int]]:
    resp = supabase.table(TABLE_NAME).select("company, year").execute()

    seen = set()
    for row in resp.data:
        company = row.get("company")
        year = row.get("year")
        if not company or not year:
            continue
        try:
            year_int = int(year)
        except (TypeError, ValueError):
            continue
        seen.add((company, year_int))

    return sorted(seen)


def main():

    print("=" * 80)
    print("BATCH KPI EXTRACTION — all companies")
    print("=" * 80)

    targets = discover_targets()

    if not targets:
        print("\nNo (company, year) pairs found in Supabase.")
        return

    print(f"\nDiscovered {len(targets)} target(s):")
    for c, y in targets:
        print(f"  - {c} {y}")
    print(f"\nWaiting {SECONDS_BETWEEN_COMPANIES}s between companies "
          f"to respect Groq's 8k TPM limit.")

    print("\nLoading embedding model...")
    embeddings = SentenceTransformerEmbeddings(EMBEDDING_MODEL)

    print("\nConnecting to Supabase vector store...")
    vector_store = SupabaseVectorStore()

    retriever = Retriever(
        vector_store=vector_store,
        embeddings=embeddings,
    )
    print("Retriever ready.")

    results_summary = []

    for i, (company, year) in enumerate(targets):

        print()
        print("=" * 80)
        print(f"EXTRACTING KPIs — {company} {year}")
        print("=" * 80)

        try:
            metrics = extract_financial_metrics(
                retriever=retriever,
                company=company,
                year=year,
            )
            print(f"\n  Extracted {len(metrics)} fields.")

            saved = False
            try:
                save_metrics(
                    company=company,
                    year=year,
                    metrics=metrics,
                )
                saved = True
                print("  PostgreSQL save successful.")
            except Exception as exc:
                print(f"  PostgreSQL save failed: {exc}")

            results_summary.append({
                "company": company,
                "year": year,
                "saved": saved,
            })

        except Exception as exc:
            print(f"  EXTRACTION FAILED: {exc}")
            results_summary.append({
                "company": company,
                "year": year,
                "saved": False,
                "error": str(exc),
            })

        # Sleep between companies to reset the TPM window
        if i < len(targets) - 1:
            print(
                f"\n[sleep] Waiting {SECONDS_BETWEEN_COMPANIES}s "
                f"before next company..."
            )
            time.sleep(SECONDS_BETWEEN_COMPANIES)

    print()
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    for r in results_summary:
        status = "saved" if r["saved"] else "FAILED"
        print(f"  {r['company']} {r['year']}: {status}")
    print()


if __name__ == "__main__":
    main()