"""PDF ingestion pipeline used by the asynchronous upload endpoint."""

import logging
import os
import re
from pathlib import Path
from services.reports import identity

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_DIR = REPO_ROOT / "data" / "markdown"
FILENAME_RE = re.compile(r"^(\d{4})_(.+)\.md$", re.IGNORECASE)
BATCH_SIZE = 50


def _progress(progress_cb, message: str) -> None:
    print(f"[pipeline] {message}")
    if progress_cb is None:
        return
    try:
        progress_cb(message)
    except Exception:
        logger.exception("Progress callback failed")


def _step(label: str, operation):
    try:
        return operation()
    except Exception as exc:
        logger.exception("Ingestion step '%s' failed: %s", label, exc)
        raise RuntimeError(f"{label} failed: {exc}") from exc


def run_ingestion(pdf_path: Path, progress_cb=None) -> dict:
    """Convert, chunk, embed, store, extract KPIs, and persist one PDF."""
    pdf_path = Path(pdf_path)
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    # Heavy and application imports stay inside the call so API startup does
    # not load the embedding stack.
    from dotenv import load_dotenv
    from supabase import create_client
    from ingestion.pdf_to_markdown import PDFToMarkdownConverter
    from ingestion.semantic_chunker import LocalEmbeddings, chunk_markdown

    load_dotenv()

    _progress(progress_cb, "Converting PDF to markdown...")
    markdown_path = Path(_step(
        "PDF conversion",
        lambda: PDFToMarkdownConverter().convert_pdf(
            pdf_path=str(pdf_path),
            output_dir=str(MARKDOWN_DIR),
        ),
    ))

    match = FILENAME_RE.fullmatch(markdown_path.name)
    if not match or not match.group(2).strip():
        message = (
            f"Markdown filename '{markdown_path.name}' must match "
            "'YYYY_CompanyName.md'; upload a file such as 2024_NVIDIA.pdf."
        )
        logger.error(message)
        raise ValueError(message)

    company, year = identity(pdf_path.name)
    if not markdown_path.is_file():
        raise FileNotFoundError(
            f"PDF conversion did not create markdown file: {markdown_path}"
        )

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_API_KEY")
    table_name = os.getenv("VECTOR_TABLE_NAME", "documents")
    embedding_model = os.getenv(
        "EMBEDDING_MODEL",
        "sentence-transformers/all-MiniLM-L6-v2",
    )
    if not supabase_url or not supabase_key:
        raise RuntimeError(
            "Supabase configuration is missing SUPABASE_URL or "
            "SUPABASE_SECRET_KEY/SUPABASE_API_KEY."
        )

    client = _step(
        "Supabase client setup",
        lambda: create_client(supabase_url, supabase_key),
    )

    _progress(progress_cb, f"Removing prior chunks for {markdown_path.name}...")
    _step(
        "Removing prior chunks",
        lambda: (
            client.table(table_name)
            .delete()
            .eq("source_file", markdown_path.name)
            .execute()
        ),
    )

    _progress(progress_cb, "Loading the embedding model...")
    embeddings = _step(
        "Embedding model setup",
        lambda: LocalEmbeddings(model_name=embedding_model),
    )

    _progress(progress_cb, "Chunking markdown...")
    chunks = _step(
        "Markdown chunking",
        lambda: chunk_markdown(
            markdown_file=str(markdown_path),
            embeddings=embeddings,
        ),
    )
    if not chunks:
        raise RuntimeError(f"No chunks were generated from {markdown_path.name}.")

    texts = [chunk.page_content for chunk in chunks]
    _progress(progress_cb, f"Generating embeddings for {len(chunks)} chunks...")
    vectors = _step(
        "Embedding generation",
        lambda: embeddings.embed_documents(texts),
    )
    if len(vectors) != len(chunks):
        raise RuntimeError(
            f"Embedding count mismatch: got {len(vectors)} vectors "
            f"for {len(chunks)} chunks."
        )

    total = len(chunks)
    for start in range(0, total, BATCH_SIZE):
        stop = min(start + BATCH_SIZE, total)
        records = [
            {
                "content": chunk.page_content,
                "metadata": {
                    "source": markdown_path.name,
                    "chunk_index": index,
                    "company": company,
                    "year": year,
                },
                "embedding": vector,
                "company": company,
                "year": str(year),
                "source_file": markdown_path.name,
            }
            for index, (chunk, vector) in enumerate(
                zip(chunks[start:stop], vectors[start:stop]),
                start=start,
            )
        ]
        _step(
            f"Supabase insert batch {start + 1}-{stop}",
            lambda records=records: (
                client.table(table_name).insert(records).execute()
            ),
        )
        _progress(progress_cb, f"Inserted {stop}/{total}")

    # Query embedding and retrieval imports are also lazy. Pass the same
    # Supabase credentials explicitly because the vector-store wrapper reads
    # SUPABASE_API_KEY by default while deployments may use the service key.
    from rag.kpi_extractor_rag import (
        EMBEDDING_MODEL,
        Retriever,
        SentenceTransformerEmbeddings,
        extract_financial_metrics,
    )
    from vectorstore.supabase_vectorstore import SupabaseVectorStore
    from database.save_metrics import save_metrics

    _progress(
        progress_cb,
        "Extracting KPIs with Groq (this takes about 30-60 seconds)...",
    )
    query_embeddings = _step(
        "KPI query embedding model setup",
        lambda: SentenceTransformerEmbeddings(EMBEDDING_MODEL),
    )
    vector_store = _step(
        "Vector store setup",
        lambda: SupabaseVectorStore(url=supabase_url, api_key=supabase_key),
    )
    retriever = Retriever(vector_store=vector_store, embeddings=query_embeddings)
    metrics = _step(
        "KPI extraction",
        lambda: extract_financial_metrics(
            retriever=retriever,
            company=company,
            year=year,
        ),
    )

    _progress(progress_cb, "Saving metrics to PostgreSQL...")
    _step(
        "PostgreSQL save",
        lambda: save_metrics(
            company=company,
            year=year,
            metrics=metrics,
        ),
    )

    _progress(progress_cb, f"Ingestion complete: {company} {year} — {total} chunks.")
    return {
        "company": company,
        "year": year,
        "chunks_inserted": total,
        "metrics": metrics,
    }

