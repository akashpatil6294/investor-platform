from pathlib import Path

from sentence_transformers import SentenceTransformer

from langchain_core.documents import Document

try:
    from langchain_text_splitters import RecursiveCharacterTextSplitter
except ImportError:
    from langchain.text_splitter import RecursiveCharacterTextSplitter


# ============================================================
# Environment / Project Paths
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
MARKDOWN_DIR = BASE_DIR / "data" / "markdown"


# ============================================================
# Local Embeddings
# ============================================================

class LocalEmbeddings:
    """
    Local embedding model using Sentence Transformers.
    Vectors are normalized so document/query sides match.
    """

    def __init__(
        self,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
    ):
        print(f"Loading embedding model: {model_name}")
        self.model = SentenceTransformer(model_name)
        print("Embedding model loaded successfully.")

    def embed_documents(
        self,
        texts: list[str],
    ) -> list[list[float]]:

        if not texts:
            return []

        embeddings = self.model.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=True,   # <-- CRITICAL FIX
            show_progress_bar=True,
        )

        return embeddings.tolist()

    def embed_query(
        self,
        text: str,
    ) -> list[float]:

        embedding = self.model.encode(
            text,
            convert_to_numpy=True,
            normalize_embeddings=True,   # <-- CRITICAL FIX
        )

        return embedding.tolist()


# ============================================================
# Read Markdown
# ============================================================

def read_markdown(markdown_file: str) -> str:

    path = Path(markdown_file)

    if not path.exists():
        raise FileNotFoundError(
            f"Markdown file not found: {path}"
        )

    content = path.read_text(encoding="utf-8")

    if not content.strip():
        raise ValueError(
            f"Markdown file is empty: {path}"
        )

    return content


# ============================================================
# Chunking
# ============================================================
#
# Replaces SemanticChunker (which produced ~4-page chunks)
# with a deterministic splitter producing ~800-character
# chunks. This keeps individual table rows retrievable.
# ============================================================

def chunk_markdown(
    markdown_file: str,
    embeddings: LocalEmbeddings = None,  # kept for API compat
) -> list[Document]:

    markdown_content = read_markdown(markdown_file)

    print("Creating chunker...")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=800,
        chunk_overlap=150,
        length_function=len,
        separators=[
            "\n\n",   # paragraph break
            "\n",     # line break (preserves table rows)
            ". ",     # sentence
            " ",      # word
            "",       # character
        ],
        is_separator_regex=False,
    )

    print("Generating chunks...")

    chunks = splitter.create_documents([markdown_content])

    return chunks


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":

    print("=" * 80)
    print("Local Chunking")
    print("=" * 80)

    embedding_model = "sentence-transformers/all-MiniLM-L6-v2"
    vector_dimension = 384

    print(f"Embedding model : {embedding_model}")
    print(f"Vector dimension: {vector_dimension}")

    embeddings = LocalEmbeddings(model_name=embedding_model)

    markdown_file = MARKDOWN_DIR / "2024_Apple.md"

    print(f"Markdown file   : {markdown_file}")

    chunks = chunk_markdown(
        markdown_file=str(markdown_file),
        embeddings=embeddings,
    )

    print()
    print("=" * 80)
    print(f"Generated {len(chunks)} chunks")
    print("=" * 80)

    sizes = [len(c.page_content) for c in chunks]
    print(f"Min chunk size: {min(sizes)}")
    print(f"Max chunk size: {max(sizes)}")
    print(f"Avg chunk size: {sum(sizes) // len(sizes)}")

    for index, chunk in enumerate(chunks[:3]):
        print()
        print("=" * 80)
        print(f"Chunk {index + 1}")
        print("=" * 80)
        print(chunk.page_content[:600])

    print()
    print("=" * 80)
    print("Chunking completed successfully.")
    print("=" * 80)