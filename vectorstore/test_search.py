from sentence_transformers import SentenceTransformer
from vectorstore.supabase_vectorstore import SupabaseVectorStore


print("=" * 80)
print("SUPABASE VECTOR SEARCH TEST")
print("=" * 80)


# Same model used to create the stored embeddings
model = SentenceTransformer(
    "sentence-transformers/all-MiniLM-L6-v2"
)


# Create Supabase connection
vector_store = SupabaseVectorStore()


# Query
query = "What was Apple's revenue in 2024?"

print(f"\nSearching for: {query}")


# Generate query embedding
query_embedding = model.encode(
    query,
    normalize_embeddings=True
).tolist()


print("Query embedding generated.")
print(f"Vector dimension: {len(query_embedding)}")


# Direct vector search
results = vector_store.similarity_search(
    query_embedding=query_embedding,
    match_count=5
)


print("\n" + "=" * 80)
print(f"FOUND {len(results)} RESULTS")
print("=" * 80)


for index, result in enumerate(results, start=1):

    print(f"\n{'-' * 80}")
    print(f"RESULT {index}")
    print(f"{'-' * 80}")

    print(f"ID: {result.get('id')}")

    print(
        f"Similarity: "
        f"{float(result.get('similarity', 0)):.4f}"
    )

    print(
        f"Metadata: "
        f"{result.get('metadata')}"
    )

    print("\nContent:")

    content = result.get("content", "")

    print(content[:1500])


print("\n" + "=" * 80)
print("VECTOR SEARCH TEST COMPLETED")
print("=" * 80)