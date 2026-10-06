"""
Batch ingestion: every .md in data/markdown → Supabase pgvector.
Skips files whose source_file already exists (idempotent).

Run from ingestion/:
    python store_embeddings_all.py
"""

import os
import re
from pathlib import Path

from dotenv import load_dotenv
from supabase import create_client, Client

from semantic_chunker import LocalEmbeddings, chunk_markdown

load_dotenv()


SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY")

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL missing from .env")
if not SUPABASE_SECRET_KEY:
    raise RuntimeError("SUPABASE_SECRET_KEY missing from .env")

EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL",
    "sentence-transformers/all-MiniLM-L6-v2",
)
TABLE_NAME = os.getenv("VECTOR_TABLE_NAME", "documents")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SECRET_KEY)

REPO_ROOT = Path(__file__).resolve().parent.parent
MARKDOWN_DIR = REPO_ROOT / "data" / "markdown"

_FILENAME_RE = re.compile(r"^(\d{4})_(.+)\.md$", re.IGNORECASE)


def parse_filename(path: Path) -> tuple[int | None, str]:
    m = _FILENAME_RE.match(path.name)
    if m:
        return int(m.group(1)), m.group(2)
    return None, path.stem


def already_ingested(source_name: str) -> bool:
    resp = (
        supabase
        .table(TABLE_NAME)
        .select("id", count="exact")
        .eq("source_file", source_name)
        .limit(1)
        .execute()
    )
    return (resp.count or 0) > 0


def ingest_file(markdown_file: Path, embeddings: LocalEmbeddings) -> int:

    year, company = parse_filename(markdown_file)

    print()
    print("=" * 80)
    print(f"FILE     : {markdown_file.name}")
    print(f"COMPANY  : {company}")
    print(f"YEAR     : {year}")
    print("=" * 80)

    if already_ingested(markdown_file.name):
        print("  SKIPPED — already ingested.")
        return 0

    chunks = chunk_markdown(
        markdown_file=str(markdown_file),
        embeddings=embeddings,
    )
    print(f"  Generated {len(chunks)} chunks")

    if not chunks:
        print("  No chunks — skipping.")
        return 0

    texts = [c.page_content for c in chunks]
    print("  Generating embeddings...")
    vectors = embeddings.embed_documents(texts)

    if len(vectors) != len(chunks):
        raise RuntimeError(
            f"Embedding count mismatch: {len(vectors)} vs {len(chunks)}"
        )

    records = []
    for index, (chunk, vector) in enumerate(zip(chunks, vectors)):
        records.append({
            "content": chunk.page_content,
            "metadata": {
                "source": markdown_file.name,
                "chunk_index": index,
                "company": company,
                "year": year,
            },
            "embedding": vector,
            "company": company,
            "year": str(year) if year is not None else None,
            "source_file": markdown_file.name,
        })

    BATCH = 50
    total = 0
    for i in range(0, len(records), BATCH):
        batch = records[i : i + BATCH]
        supabase.table(TABLE_NAME).insert(batch).execute()
        total += len(batch)
        print(f"  Inserted {total}/{len(records)}")

    return total


def main():

    print("=" * 80)
    print("BATCH INGESTION — all markdown files → Supabase")
    print("=" * 80)
    print(f"Markdown dir : {MARKDOWN_DIR}")
    print(f"Table        : {TABLE_NAME}")
    print(f"Embedding    : {EMBEDDING_MODEL}")

    if not MARKDOWN_DIR.exists():
        raise FileNotFoundError(f"Markdown dir not found: {MARKDOWN_DIR}")

    files = sorted(MARKDOWN_DIR.glob("*.md"))

    if not files:
        print("\nNo .md files found.")
        return

    print(f"\nFound {len(files)} markdown file(s):")
    for f in files:
        print(f"  - {f.name}")

    print("\nLoading embedding model...")
    embeddings = LocalEmbeddings(model_name=EMBEDDING_MODEL)

    grand_total = 0
    for f in files:
        try:
            n = ingest_file(f, embeddings)
            grand_total += n
        except Exception as exc:
            print(f"\n  ERROR ingesting {f.name}: {exc}")

    print()
    print("=" * 80)
    print(f"DONE — inserted {grand_total} chunks total")
    print("=" * 80)


if __name__ == "__main__":
    main()