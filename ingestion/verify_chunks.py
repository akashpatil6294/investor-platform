import os

from dotenv import load_dotenv
from supabase import create_client

# Plain load_dotenv() auto-discovers .env (proven to work
# in the diagnostic you just ran).
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = (
    os.getenv("SUPABASE_SECRET_KEY")
    or os.getenv("SUPABASE_SERVICE_KEY")
    or os.getenv("SUPABASE_KEY")
)

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL missing from .env")
if not SUPABASE_KEY:
    raise RuntimeError(
        "No Supabase key found. Checked "
        "SUPABASE_SECRET_KEY, SUPABASE_SERVICE_KEY, SUPABASE_KEY"
    )

client = create_client(SUPABASE_URL, SUPABASE_KEY)

TABLE = os.getenv("VECTOR_TABLE_NAME", "documents")

print(f"Using table: {TABLE}")

resp = client.table(TABLE).select("metadata, content").execute()
rows = resp.data

print(f"Total rows in table: {len(rows)}")

sizes = [len(r.get("content", "")) for r in rows]
if sizes:
    print(f"Min chunk size : {min(sizes)}")
    print(f"Max chunk size : {max(sizes)}")
    print(f"Avg chunk size : {sum(sizes) // len(sizes)}")

# ------------------------------------------------------------
# Check for known Apple 2024 figures
# ------------------------------------------------------------

FIGURES = {
    "Revenue (Total net sales)": "391,035",
    "Net income": "93,736",
    "Operating income": "123,216",
    "Cash from operating activities": "118,254",
    "Total assets": "364,980",
    "Total liabilities": "308,030",
}

print()
print("=" * 80)
print("FIGURE SEARCH")
print("=" * 80)

for label, figure in FIGURES.items():
    hits = [
        r for r in rows
        if figure in r.get("content", "")
    ]
    print()
    print(f"{label} — '{figure}': {len(hits)} chunk(s)")

    for r in hits[:3]:
        meta = r.get("metadata", {})
        idx = meta.get("chunk_index")
        length = len(r["content"])
        print(f"  chunk_index={idx} len={length}")
        content = r["content"]
        pos = content.find(figure)
        start = max(0, pos - 100)
        end = min(len(content), pos + 100)
        snippet = content[start:end].replace("\n", " ")
        print(f"  ...{snippet}...")