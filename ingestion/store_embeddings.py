from pathlib import Path
import os

from dotenv import load_dotenv
from supabase import create_client, Client

from semantic_chunker import (
    LocalEmbeddings,
    chunk_markdown,
)

# Load environment variables


load_dotenv()


# Configuration


SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY")

EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL",
    "sentence-transformers/all-MiniLM-L6-v2",
)

VECTOR_DIMENSION = int(
    os.getenv(
        "VECTOR_DIMENSION",
        "384",
    )
)

TABLE_NAME = os.getenv(
    "VECTOR_TABLE_NAME",
    "documents",
)


# ============================================================
# Validate configuration
# ============================================================

if not SUPABASE_URL:
    raise RuntimeError(
        "SUPABASE_URL is missing from .env"
    )

if not SUPABASE_SECRET_KEY:
    raise RuntimeError(
        "SUPABASE_SECRET_KEY is missing from .env"
    )


# ============================================================
# Create Supabase client
# ============================================================

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_SECRET_KEY,
)


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":

    print("=" * 80)
    print("SUPABASE VECTOR STORE")
    print("=" * 80)

    print(f"Embedding model : {EMBEDDING_MODEL}")
    print(f"Vector dimension: {VECTOR_DIMENSION}")
    print(f"Table           : {TABLE_NAME}")

    # --------------------------------------------------------
    # Locate Markdown file
    # --------------------------------------------------------

    repo_root = Path(__file__).resolve().parents[1]

    markdown_file = (
        repo_root
        / "data"
        / "markdown"
        / "2024_Apple.md"
    )

    if not markdown_file.exists():
        raise FileNotFoundError(
            f"Markdown file not found: {markdown_file}"
        )

    print()
    print(f"Markdown file: {markdown_file}")

    # --------------------------------------------------------
    # Load embedding model
    # --------------------------------------------------------

    print()
    print("Loading embedding model...")

    embeddings = LocalEmbeddings(
        model_name=EMBEDDING_MODEL
    )

    # --------------------------------------------------------
    # Generate semantic chunks
    # --------------------------------------------------------

    print()
    print("Creating semantic chunks...")

    chunks = chunk_markdown(
        markdown_file=str(markdown_file),
        embeddings=embeddings,
    )

    print()
    print(f"Generated {len(chunks)} chunks")

    if not chunks:
        raise RuntimeError(
            "No chunks were generated."
        )

    # --------------------------------------------------------
    # Extract chunk text
    # --------------------------------------------------------

    texts = [
        chunk.page_content
        for chunk in chunks
    ]

    # --------------------------------------------------------
    # Generate embeddings in one local batch
    # --------------------------------------------------------

    print()
    print("Generating embeddings...")

    vectors = embeddings.embed_documents(texts)

    if len(vectors) != len(chunks):
        raise RuntimeError(
            "Number of embeddings does not match "
            "number of chunks."
        )

    print(
        f"Generated {len(vectors)} embeddings."
    )

    # --------------------------------------------------------
    # Prepare Supabase records
    # --------------------------------------------------------

    print()
    print("Preparing database records...")

    records = []

    for index, (chunk, vector) in enumerate(
        zip(chunks, vectors)
    ):

        record = {
    "content": chunk.page_content,

    "metadata": {
        "source": markdown_file.name,
        "chunk_index": index,
    },

    "embedding": vector,

    "company": "Apple",

    "year": "2024",

    "source_file": markdown_file.name,
}

        records.append(record)

    # --------------------------------------------------------
    # Upload to Supabase
    # --------------------------------------------------------

    print()
    print("Uploading records to Supabase...")

    response = (
        supabase
        .table(TABLE_NAME)
        .insert(records)
        .execute()
    )

  
    # Success
   

    print()
    print("=" * 80)
    print("UPLOAD COMPLETED SUCCESSFULLY")
    print("=" * 80)

    print(
        f"Inserted {len(records)} chunks "
        f"into '{TABLE_NAME}'."
    )