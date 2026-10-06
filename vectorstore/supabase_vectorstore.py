import os
from typing import Optional

from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()


class SupabaseVectorStore:
    """
    Vector store backed by Supabase PostgreSQL + pgvector.
    """

    def __init__(
        self,
        url: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.url = url or os.getenv("SUPABASE_URL")
        self.api_key = api_key or os.getenv("SUPABASE_API_KEY") or os.getenv("SUPABASE_SECRET_KEY")

        if not self.url:
            raise RuntimeError("Missing SUPABASE_URL in .env")

        if not self.api_key:
            raise RuntimeError("Missing SUPABASE_API_KEY in .env")

        self.client: Client = create_client(
            self.url,
            self.api_key
        )

    def upload_chunks(
        self,
        chunks,
        embeddings,
        company: str,
        year: str,
        source_file: str,
    ):
        """
        Generate embeddings and upload document chunks to Supabase.
        """

        if not chunks:
            print("No chunks to upload.")
            return

        print(f"Generating embeddings for {len(chunks)} chunks...")

        texts = [
            chunk.page_content
            for chunk in chunks
        ]

        vectors = embeddings.embed_documents(texts)

        if len(vectors) != len(chunks):
            raise RuntimeError(
                "Number of embeddings does not match number of chunks."
            )

        records = []

        for index, (chunk, vector) in enumerate(
            zip(chunks, vectors)
        ):
            records.append(
                {
                    "content": chunk.page_content,

                    "metadata": {
                        **chunk.metadata,
                        "company": company,
                        "year": str(year),
                        "chunk_index": index,
                        "source": source_file,
                    },

                    "embedding": vector,

                    "company": company,
                    "year": str(year),
                    "source_file": source_file,
                }
            )

        print(f"Uploading {len(records)} chunks to Supabase...")

        response = (
            self.client
            .table("documents")
            .insert(records)
            .execute()
        )

        print(
            f"Successfully uploaded "
            f"{len(response.data)} chunks."
        )

        return response.data

    def similarity_search(
        self,
        query_embedding,
        match_count: int = 5,
        company: Optional[str] = None,
        year: Optional[int] = None,
    ):
        """
        Search Supabase using vector similarity.
        """

        # Filter BEFORE nearest-neighbor LIMIT. Parameterized SQL avoids relying
        # on a deployed RPC signature and preserves the old ingestion interface.
        if company is not None or year is not None:
            import json
            from sqlalchemy import text
            from database.postgres_sql import get_engine
            from services.financial import canonical_company
            query = text('''SELECT content, metadata, company, year, source_file,
                1 - (embedding <=> CAST(:embedding AS vector)) AS similarity
                FROM documents
                WHERE (:company IS NULL OR lower(company) = lower(:company))
                  AND (:year IS NULL OR year::text = :year)
                ORDER BY embedding <=> CAST(:embedding AS vector)
                LIMIT :count''')
            with get_engine().connect() as connection:
                return [dict(row._mapping) for row in connection.execute(query, {
                    'embedding': json.dumps(query_embedding), 'company': canonical_company(company) if company else None,
                    'year': str(year) if year is not None else None, 'count': match_count})]
        response = self.client.rpc(
            "match_documents",
            {
                "query_embedding": query_embedding,
                "match_count": match_count,
            }
        ).execute()

        return response.data


class Retriever:
    """
    Simple retriever used by the RAG pipeline.
    """

    def __init__(
        self,
        vector_store: SupabaseVectorStore,
        embeddings,
    ):
        self.vector_store = vector_store
        self.embeddings = embeddings

    def retrieve(
        self,
        query: str,
        k: int = 5,
        company: Optional[str] = None,
        year: Optional[int] = None,
    ):
        """
        Convert query into an embedding and perform
        semantic vector search.
        """

        print(f"Searching for: {query}")

        query_embedding = self.embeddings.embed_query(query)

        results = self.vector_store.similarity_search(
            query_embedding=query_embedding,
            match_count=k,
            company=company,
            year=year,
        )

        return results
