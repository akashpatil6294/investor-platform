# rag/kpi_extractor_rag.py

import os
import json
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from sentence_transformers import SentenceTransformer

from vectorstore.supabase_vectorstore import SupabaseVectorStore
from database.save_metrics import save_metrics


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()


# ============================================================
# CONFIGURATION
# ============================================================

EMBEDDING_MODEL = os.getenv(
    "EMBEDDING_MODEL",
    "sentence-transformers/all-MiniLM-L6-v2"
)

GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "openai/gpt-oss-20b"
)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# 14,000 chars ≈ 4,700 tokens for context.
# Prompt template ≈ 2,000 tokens.
# Total ≈ 6,700 — well under Groq free-tier 8,000 TPM.
MAX_CONTEXT_CHARS = int(
    os.getenv("MAX_CONTEXT_CHARS", "14000")
)

# Per-chunk character cap for non-priority (narrative) chunks.
# Priority chunks (cash flow, balance sheet totals) keep
# their full content because the key rows are often past
# character 700.
MAX_CHARS_PER_CHUNK = int(
    os.getenv("MAX_CHARS_PER_CHUNK", "700")
)

TOP_K_PER_QUERY = int(
    os.getenv("TOP_K_PER_QUERY", "20")
)

MAX_FINAL_CHUNKS = int(
    os.getenv("MAX_FINAL_CHUNKS", "25")
)

TOP_BY_SIMILARITY = int(
    os.getenv("TOP_BY_SIMILARITY", "12")
)

EXPAND_NEIGHBORS = (
    os.getenv("EXPAND_NEIGHBORS", "true").lower() == "true"
)

# Completion token cap for Groq. Without this Groq uses a
# default that's often too small for the full JSON schema,
# causing "max completion tokens reached before generating
# a valid document".
MAX_COMPLETION_TOKENS = int(
    os.getenv("MAX_COMPLETION_TOKENS", "2500")
)


# ============================================================
# VALIDATION
# ============================================================

if not GROQ_API_KEY:
    raise RuntimeError("Missing GROQ_API_KEY in .env")


# ============================================================
# PYDANTIC OUTPUT MODEL
# ============================================================

class FinancialMetrics(BaseModel):
    revenue: Optional[str | int | float] = Field(default=None, alias="Revenue")
    net_income: Optional[str | int | float] = Field(default=None, alias="Net Income")
    operating_income: Optional[str | int | float] = Field(default=None, alias="Operating Income")
    cash_flow: Optional[str | int | float] = Field(
        default=None, alias="Cash Flow from Operating Activities"
    )
    total_assets: Optional[str | int | float] = Field(default=None, alias="Total Assets")
    total_liabilities: Optional[str | int | float] = Field(default=None, alias="Total Liabilities")
    risk_factors: Optional[str | list] = Field(default=None, alias="Top Risk Factors")
    growth_drivers: Optional[str | list] = Field(default=None, alias="Top Growth Drivers")


# ============================================================
# EMBEDDING WRAPPER
# ============================================================

class SentenceTransformerEmbeddings:

    def __init__(self, model_name: str):
        print("=" * 80)
        print("LOADING EMBEDDING MODEL")
        print("=" * 80)
        print(f"Model: {model_name}")

        self.model = SentenceTransformer(model_name)

        try:
            dim = self.model.get_embedding_dimension()
        except AttributeError:
            dim = self.model.get_sentence_embedding_dimension()

        print(f"Embedding dimension: {dim}")
        print("Embedding model loaded.")

    def embed_query(self, text: str) -> list[float]:
        vector = self.model.encode(text, normalize_embeddings=True)
        return vector.tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors = self.model.encode(texts, normalize_embeddings=True)
        return vectors.tolist()


# ============================================================
# RETRIEVER
# ============================================================

class Retriever:

    def __init__(
        self,
        vector_store: SupabaseVectorStore,
        embeddings: SentenceTransformerEmbeddings
    ):
        self.vector_store = vector_store
        self.embeddings = embeddings

    def retrieve(
        self,
        query: str,
        k: int = TOP_K_PER_QUERY
    ) -> list[dict]:

        query_embedding = self.embeddings.embed_query(query)

        results = self.vector_store.similarity_search(
            query_embedding=query_embedding,
            match_count=k
        )

        return results


# ============================================================
# SEARCH QUERIES
# ============================================================
#
# Phrasing note:
# These queries deliberately match the LANGUAGE OF FINANCIAL
# STATEMENT TABLES ("Total net sales", "Cost of sales",
# "Total assets", "Cash generated by operating activities"),
# not narrative discussion.
#
# They do NOT include the company name. Company isolation
# happens in retrieve_context() via a source-based filter.
# ============================================================

def build_search_queries(company: str, year: int) -> list[str]:
    return [
        # 1) INCOME STATEMENT — total net sales / total revenues
        (
            "Consolidated Statements of Operations "
            "Total net sales net sales total revenues revenue "
            "Products Services "
            "Cost of sales cost of revenue Gross margin "
            "years ended in millions"
        ),

        # 2) INCOME STATEMENT — operating income
        (
            "Operating income income from operations "
            "Total operating expenses "
            "research and development "
            "selling general and administrative"
        ),

        # 3) INCOME STATEMENT — net income
        (
            "Net income net earnings net loss "
            "earnings per share basic diluted "
            "Net income attributable net income per share"
        ),

        # 4) BALANCE SHEET — total assets
        (
            "Consolidated Balance Sheets "
            "Total assets Total current assets "
            "cash and cash equivalents "
            "marketable securities accounts receivable inventories "
            "Total non-current assets property and equipment"
        ),

        # 5) BALANCE SHEET — total liabilities & equity
        (
            "Total liabilities current liabilities "
            "term debt commercial paper accounts payable "
            "long-term debt "
            "Total non-current liabilities "
            "Total shareholders equity stockholders equity "
            "Total liabilities and shareholders equity "
            "Total liabilities and stockholders equity"
        ),

        # 5b) BALANCE SHEET — dedicated totals line
        (
            "Total assets Total liabilities "
            "Total shareholders equity Total stockholders equity "
            "Total liabilities and shareholders equity "
            "Total liabilities and stockholders equity "
            "consolidated balance sheet"
        ),

        # 6) CASH FLOW — operating activities (many phrasings)
        (
            "Consolidated Statements of Cash Flows "
            "Cash generated by operating activities "
            "cash provided by operating activities "
            "cash provided by (used in) operating activities "
            "net cash provided by operating activities "
            "net cash provided by (used in) operating activities "
            "net cash flows from operating activities "
            "net cash from operations "
            "net cash from operating activities "
            "cash flows from operating activities "
            "Net income adjustments to reconcile "
            "cash and cash equivalents"
        ),

        # 7) CASH FLOW — investing and financing
        (
            "Net cash used in investing activities "
            "Net cash provided by financing activities "
            "Net cash used in financing activities "
            "Net increase decrease in cash "
            "capital expenditures "
            "free cash flow "
            "Investing activities Financing activities"
        ),

        # 8) RISK FACTORS — Item 1A phrasing
        (
            "Item 1A Risk Factors "
            "The Company's business reputation results of "
            "operations and financial condition can be "
            "materially and adversely affected by "
            "the Company's ability to offer products and services "
            "competitive in the markets"
        ),

        # 9) BUSINESS / GROWTH DRIVERS
        (
            "Products and Services Performance "
            "net sales by category "
            "iPhone Mac iPad Wearables Home and Accessories "
            "Services net sales increased higher demand "
            "installed base customers geographic segments"
        ),

        # 10) RISK FACTORS — macro + operational
        (
            "Risks related to the Company's business and industry "
            "global macroeconomic conditions "
            "supply chain disruptions manufacturing partners "
            "component shortages tariffs trade restrictions "
            "privacy regulatory data protection cybersecurity "
            "intellectual property litigation"
        ),
    ]


# ============================================================
# HELPERS
# ============================================================

def _safe_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _chunk_index(result: dict):
    meta = result.get("metadata") or {}
    return meta.get("chunk_index")


def _content_of(result: dict) -> str:
    return (
        result.get("content")
        or result.get("page_content")
        or ""
    )


def _source_of(result: dict) -> str:
    meta = result.get("metadata") or {}
    return str(
        meta.get("source")
        or result.get("source_file")
        or ""
    ).lower()


# ============================================================
# RETRIEVE CONTEXT
# ============================================================

def retrieve_context(
    retriever: Retriever,
    company: str,
    year: int
) -> str:

    print("\n" + "=" * 80)
    print("RETRIEVING FINANCIAL CONTEXT")
    print("=" * 80)

    queries = build_search_queries(company, year)
    print(f"\nTotal targeted queries: {len(queries)}")

    # --------------------------------------------------------
    # Run each query independently
    # --------------------------------------------------------

    per_query_results = []

    for index, query in enumerate(queries, start=1):

        print(f"\nSEARCH {index}/{len(queries)}")
        print("-" * 80)
        print(f"Query: {query.strip().replace(chr(10), ' ')[:160]}")

        try:
            results = retriever.retrieve(
                query=query, k=TOP_K_PER_QUERY
            )
            print(f"Retrieved {len(results)} chunks.")

            for r_idx, r in enumerate(results, start=1):
                meta = r.get("metadata", {})
                sim = _safe_float(r.get("similarity", 0))
                print(f"  [{r_idx}] sim={sim:.4f} meta={meta}")

            per_query_results.append(results)

        except Exception as exc:
            print(f"Search {index} failed: {exc}")
            per_query_results.append([])

    # --------------------------------------------------------
    # Global dedup with max similarity
    # --------------------------------------------------------

    seen_content = {}

    for results in per_query_results:
        for r in results:
            content = _content_of(r).strip()
            if not content:
                continue

            sim = _safe_float(r.get("similarity", 0))

            if content not in seen_content:
                seen_content[content] = dict(r)
                seen_content[content]["_sim"] = sim
            else:
                if sim > seen_content[content]["_sim"]:
                    seen_content[content]["_sim"] = sim

    pool = list(seen_content.values())
    pool.sort(key=lambda x: x["_sim"], reverse=True)

    print("\n" + "=" * 80)
    print(f"TOTAL UNIQUE CHUNKS (before company filter): {len(pool)}")
    print("=" * 80)

    # --------------------------------------------------------
    # Company filter
    # --------------------------------------------------------

    company_lc = (company or "").lower().strip()

    if company_lc:
        company_pool = [
            c for c in pool
            if company_lc in _source_of(c)
        ]

        if company_pool:
            print(
                f"COMPANY FILTER: {len(pool)} → "
                f"{len(company_pool)} chunks "
                f"for '{company}'"
            )
            pool = company_pool
        else:
            print(
                f"WARNING: no chunks matched company "
                f"'{company}' in their source. "
                f"Using full pool."
            )
    else:
        print("No company filter applied.")

    # --------------------------------------------------------
    # Build index → chunk lookup for neighbor expansion
    # --------------------------------------------------------

    pool_by_index = {}
    for c in pool:
        idx = _chunk_index(c)
        if idx is not None and idx not in pool_by_index:
            pool_by_index[idx] = c

    # --------------------------------------------------------
    # Step 1: top-by-similarity
    # --------------------------------------------------------

    selected = pool[:TOP_BY_SIMILARITY]
    selected_contents = {_content_of(c) for c in selected}

    # --------------------------------------------------------
    # Step 2: coverage — each query contributes top new chunk
    # --------------------------------------------------------

    for q_idx, results in enumerate(per_query_results, start=1):

        new_chunk = None
        for r in results:
            content = _content_of(r).strip()
            if not content:
                continue
            if content in selected_contents:
                continue
            if company_lc and company_lc not in _source_of(r):
                continue
            new_chunk = r
            break

        if new_chunk is None:
            continue

        selected.append({
            **new_chunk,
            "_sim": _safe_float(new_chunk.get("similarity", 0)),
        })
        selected_contents.add(_content_of(new_chunk))

        print(
            f"  + coverage: query {q_idx} added "
            f"chunk_index={_chunk_index(new_chunk)}"
        )

    # --------------------------------------------------------
    # Step 3: priority pass — force-include statement totals
    # --------------------------------------------------------
    #
    # Cash flow and balance sheet total rows frequently rank
    # below narrative chunks for similarity but are the only
    # chunks that carry the actual numbers the LLM needs.
    #
    # Keywords cover every major 10-K phrasing:
    #   Apple:      "Cash generated by operating activities"
    #   Tesla:      "Net cash provided by operating activities"
    #   Amazon:     "Net cash provided by (used in) operating activities"
    #   Microsoft:  "Net cash from operations"
    # --------------------------------------------------------

    priority_keywords = [
        # Cash flow — every major 10-K phrasing
        "cash generated by operating",
        "cash provided by operating",
        "cash provided by (used in) operating",
        "cash used in operating",
        "cash flows from operating",
        "cash flows provided by operating",
        "cash flows from operations",
        "net cash from operations",
        "net cash from operating",
        "net cash provided by operating",
        "net cash provided by (used in) operating",
        "net cash used in operating",
        "net cash flows from operating",
        "net cash flows provided by operating",
        "net cash generated from operating",
        "operating cash flow",

        # Balance sheet totals
        "total assets",
        "total liabilities and shareholders",
        "total liabilities and stockholders",
        "total shareholders' equity",
        "total shareholders equity",
        "total stockholders' equity",
        "total stockholders equity",
        "total current liabilities",
        "total non-current liabilities",
        "total noncurrent liabilities",
        "total long-term liabilities",
    ]

    already_indices = {
        _chunk_index(c)
        for c in selected
        if _chunk_index(c) is not None
    }

    for chunk in pool:
        if len(selected) >= MAX_FINAL_CHUNKS:
            break

        idx = _chunk_index(chunk)
        if idx in already_indices:
            continue

        content_lc = _content_of(chunk).lower()
        if any(kw in content_lc for kw in priority_keywords):
            selected.append(chunk)
            selected_contents.add(_content_of(chunk))
            already_indices.add(idx)
            print(
                f"  + priority: added chunk_index={idx}"
            )

    # --------------------------------------------------------
    # Step 4: neighbor expansion
    # --------------------------------------------------------

    if EXPAND_NEIGHBORS:

        current_indices = {
            _chunk_index(c)
            for c in selected
            if _chunk_index(c) is not None
        }

        new_neighbors = []

        for c in list(selected):
            idx = _chunk_index(c)
            if idx is None:
                continue

            for neighbor_idx in (idx - 1, idx + 1):
                if neighbor_idx in current_indices:
                    continue
                neighbor = pool_by_index.get(neighbor_idx)
                if neighbor is None:
                    continue

                content = _content_of(neighbor)
                if not content or content in selected_contents:
                    continue

                new_neighbors.append(neighbor)
                current_indices.add(neighbor_idx)
                selected_contents.add(content)

                print(
                    f"  + neighbor of {idx}: added "
                    f"chunk_index={neighbor_idx}"
                )

        selected.extend(new_neighbors)

    # --------------------------------------------------------
    # Step 5: fill remaining slots by similarity
    # --------------------------------------------------------

    for chunk in pool:
        if len(selected) >= MAX_FINAL_CHUNKS:
            break

        content = _content_of(chunk)
        if content in selected_contents:
            continue

        selected.append(chunk)
        selected_contents.add(content)

    selected = selected[:MAX_FINAL_CHUNKS]

    # --------------------------------------------------------
    # Reorder so priority chunks (cash flow, balance sheet
    # totals) come FIRST. Without this, the char-budget loop
    # below truncates them out and the LLM never sees the
    # numbers it needs.
    # --------------------------------------------------------

    def _is_priority(c):
        content_lc = _content_of(c).lower()
        return any(kw in content_lc for kw in priority_keywords)

    priority_selected = [c for c in selected if _is_priority(c)]
    other_selected = [c for c in selected if not _is_priority(c)]
    selected = priority_selected + other_selected

    print(
        f"  [reorder] {len(priority_selected)} priority chunks "
        f"moved to front"
    )

    print(f"\nSELECTED CHUNKS (post-selection): {len(selected)}")
    print(
        "Selected chunk_index values: "
        + ", ".join(
            str(_chunk_index(c)) for c in selected
        )
    )

    # --------------------------------------------------------
    # Build context under char budget
    # --------------------------------------------------------

    context_parts = []
    current_size = 0

    for index, result in enumerate(selected, start=1):

        # Priority chunks keep full content — the cash flow
        # row is often past character 700.
        # Narrative chunks are truncated to save budget.
        if _is_priority(result):
            content = _content_of(result)
        else:
            content = _content_of(result)[:MAX_CHARS_PER_CHUNK]

        if not content:
            continue

        similarity = result.get(
            "_sim", result.get("similarity", 0)
        )
        metadata = result.get("metadata", {}) or {}
        source = metadata.get(
            "source", result.get("source_file", "")
        )
        chunk_index = metadata.get(
            "chunk_index", result.get("chunk_index", "")
        )

        chunk_text = f"""
--- RETRIEVED CHUNK {index} ---
Source: {source}
Chunk index: {chunk_index}
Similarity: {similarity}

{content}
"""

        if current_size + len(chunk_text) > MAX_CONTEXT_CHARS:
            break

        context_parts.append(chunk_text)
        current_size += len(chunk_text)

    context = "\n".join(context_parts)

    print(f"\nFINAL CONTEXT SIZE: {len(context):,} characters")

    if not context.strip():
        raise RuntimeError("No relevant context was retrieved.")

    return context


# ============================================================
# EXTRACTION PROMPT
# ============================================================

def build_extraction_prompt(
    company: str,
    year: int,
    context: str
) -> str:

    return f"""
You are an expert financial analyst extracting structured
information from a company's annual report.

COMPANY:
{company}

FISCAL YEAR:
{year}

IMPORTANT:
Use ONLY the retrieved annual-report context below.
Do NOT use outside knowledge.
Do NOT guess.
If a requested value cannot be supported by the context,
return null.

============================================================
REQUIRED FINANCIAL METRICS
============================================================

Extract:

1. Revenue
2. Net Income
3. Operating Income
4. Cash Flow from Operating Activities
5. Total Assets
6. Total Liabilities
7. Top Risk Factors
8. Top Growth Drivers

============================================================
CRITICAL: CASH FLOW EXTRACTION RULES
============================================================

The label for "Cash Flow from Operating Activities"
varies by company. Look for ANY of these row labels:

    "Cash generated by operating activities"                (Apple style)
    "Net cash provided by operating activities"             (Tesla style)
    "Net cash provided by (used in) operating activities"   (Amazon style)
    "Net cash from operations"                              (Microsoft style)
    "Net cash flows from operating activities"
    "Cash flows from operating activities"
    "Net cash generated from operating activities"

CRITICAL: If the chunk contains MULTIPLE "Net cash ..."
rows (investing, financing, operating), extract ONLY the
"operating activities" row. NEVER extract the "investing"
or "financing" values.

Example chunk:
    |Net cash provided by operating activities|$ 14,923|$ 13,256|
    |Net cash used in investing activities|$ (18,787)|$ (15,584)|
    |Net cash provided by (used in) financing activities|$ 3,853|$ 2,589|

From this chunk, the correct answer is 14923.

The value for fiscal year {year} is the FIRST number
after the row label (typically the leftmost year column).

============================================================
CRITICAL: TOTAL vs SUB-TOTAL RULES
============================================================

Balance sheet statements have many "total" rows. Return
the FULL TOTAL, never a sub-total.

CORRECT:
  - "Total assets" (last line of the Assets section)
  - "Total liabilities" (the FULL total)
  - "Total shareholders' equity" / "Total stockholders' equity"

WRONG — DO NOT return these as the total:
  - "Total current assets" (sub-total)
  - "Total current liabilities" alone (sub-total)
  - "Total non-current liabilities" alone (sub-total)
  - "Total segment X" (segment-level)

FALLBACK — allowed only when the exact "Total liabilities"
row is not visible:
  - If BOTH "Total current liabilities" AND
    "Total non-current liabilities" appear as separate
    rows in the same context, sum them and report that
    as Total Liabilities.
  - Mark your reasoning in the answer as
    "(sum of current + non-current)".

If you cannot determine the total either way, return null.

============================================================
HINTS FOR OTHER VALUES
============================================================

- Revenue may appear as: "Total net sales", "Net sales",
  "Total revenues", "Revenue", "Total revenue",
  "Net product sales + Net service sales".
- Operating income may appear as: "Operating income",
  "Income from operations", "Operating profit".
- Net income may appear as: "Net income", "Net earnings",
  "Net income (loss)".
- Total assets / Total liabilities appear near the BOTTOM
  of the Balance Sheet under "TOTAL ASSETS" and
  "Total liabilities and shareholders' equity".

All figures are in millions of USD, unlabelled.

============================================================
FINANCIAL VALUE RULES
============================================================

- Use the exact reported value.
- Do not calculate values (except the current + non-current
  fallback above).
- Do not convert currencies.
- Do not change units.
- Return only the number for numeric financial fields.
- Do not include "$", commas, "million", or "billion".

Example: If the report says "Revenue: $391,035 million",
return 391035 (not "$391,035", not 391035000000).

============================================================
RISK FACTORS
============================================================

Scan the entire context for risk-related text. Extract up
to 3 DISTINCT risk topics. Each item must name the risk
topic AND include a short supporting clause.

Only return null if NO risk-related text exists.

============================================================
GROWTH DRIVERS
============================================================

Return up to 3 concise items. Each item should name a
specific product, service, or segment AND the direction
of change relative to the prior year. Example:

  "Services net sales increased 13% to $96,169M"
  "AWS segment operating income grew 62% to $39,834M"
  "Automotive revenue declined 5%"

============================================================
OUTPUT
============================================================

Return valid JSON matching the required schema.

============================================================
RETRIEVED CONTEXT
============================================================

{context}
"""


# ============================================================
# GROQ CLIENT
# ============================================================

def get_groq_client():
    from groq import Groq
    return Groq(api_key=GROQ_API_KEY)


# ============================================================
# GROQ STRUCTURED EXTRACTION
# ============================================================

def get_structured_completion(prompt: str) -> FinancialMetrics:

    client = get_groq_client()

    print("\nSending retrieved context to Groq...")

    schema = {
        "type": "object",
        "properties": {
            "Revenue": {"type": ["number", "string", "null"]},
            "Net Income": {"type": ["number", "string", "null"]},
            "Operating Income": {"type": ["number", "string", "null"]},
            "Cash Flow from Operating Activities": {"type": ["number", "string", "null"]},
            "Total Assets": {"type": ["number", "string", "null"]},
            "Total Liabilities": {"type": ["number", "string", "null"]},
            "Top Risk Factors": {
                "type": ["array", "string", "null"],
                "items": {"type": "string"}
            },
            "Top Growth Drivers": {
                "type": ["array", "string", "null"],
                "items": {"type": "string"}
            }
        },
        "required": [
            "Revenue",
            "Net Income",
            "Operating Income",
            "Cash Flow from Operating Activities",
            "Total Assets",
            "Total Liabilities",
            "Top Risk Factors",
            "Top Growth Drivers"
        ],
        "additionalProperties": False
    }

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You extract financial information "
                    "from annual reports. "
                    "Use only the supplied context. "
                    "Never guess. "
                    "For cash flow, extract ONLY the "
                    "'operating activities' row, never "
                    "'investing' or 'financing'. "
                    "When asked for 'Total X', return the "
                    "FULL TOTAL, never a sub-total. "
                    "If the full total row is missing but both "
                    "current and non-current sub-totals are "
                    "present, sum them."
                )
            },
            {"role": "user", "content": prompt}
        ],
        temperature=0,
        max_completion_tokens=MAX_COMPLETION_TOKENS,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "financial_metrics",
                "strict": True,
                "schema": schema
            }
        }
    )

    print("Groq response received.")

    content = response.choices[0].message.content

    if not content:
        raise RuntimeError("Groq returned an empty response.")

    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        print("\nInvalid JSON returned by Groq:")
        print(content)
        raise RuntimeError("Groq returned invalid JSON.") from exc

    return FinancialMetrics.model_validate(data)


# ============================================================
# CLEAN METRICS
# ============================================================

def clean_metric_value(value):
    if value is None:
        return None

    if isinstance(value, list):
        return [
            str(item).strip()
            for item in value
            if str(item).strip()
        ]

    if isinstance(value, (int, float)):
        return value

    value = str(value).strip()
    if not value:
        return None

    cleaned = value.replace("$", "").replace(",", "").strip()

    try:
        number = float(cleaned)
        if number.is_integer():
            return int(number)
        return number
    except ValueError:
        return value


def clean_metrics(metrics: FinancialMetrics) -> dict:
    raw = metrics.model_dump(by_alias=True)
    return {k: clean_metric_value(v) for k, v in raw.items()}


# ============================================================
# EXTRACT FINANCIAL METRICS
# ============================================================

def extract_financial_metrics(
    retriever: Retriever,
    company: str,
    year: int
) -> dict:

    context = retrieve_context(
        retriever=retriever,
        company=company,
        year=year
    )

    print("\nBuilding financial extraction prompt...")

    prompt = build_extraction_prompt(
        company=company,
        year=year,
        context=context
    )

    metrics = get_structured_completion(prompt=prompt)
    return clean_metrics(metrics)


# ============================================================
# DISPLAY RESULTS
# ============================================================

def print_metrics(company: str, year: int, metrics: dict):

    print("\n" + "=" * 80)
    print(f"EXTRACTED KPIs — {company} {year}")
    print("=" * 80)

    fields = [
        "Revenue",
        "Net Income",
        "Operating Income",
        "Cash Flow from Operating Activities",
        "Total Assets",
        "Total Liabilities",
        "Top Risk Factors",
        "Top Growth Drivers"
    ]

    for field in fields:
        print(f"\n{field}:")
        print(metrics.get(field))

    print("\n" + "=" * 80)


# ============================================================
# SAVE TO POSTGRESQL
# ============================================================

def save_to_database(company: str, year: int, metrics: dict):

    print("\nSaving financial metrics to PostgreSQL...")

    try:
        save_metrics(company=company, year=year, metrics=metrics)
        print("PostgreSQL save successful.")
        return True
    except Exception as exc:
        print("\nWARNING: PostgreSQL save failed:")
        print(exc)
        return False


# ============================================================
# MAIN RAG PIPELINE
# ============================================================

def run_rag_pipeline(
    company: str,
    year: int,
    save_to_postgres: bool = True
) -> dict:

    print("\n" + "=" * 80)
    print("FINANCIAL RAG PIPELINE")
    print("=" * 80)
    print(f"Company  : {company}")
    print(f"Year     : {year}")
    print(f"LLM      : {GROQ_MODEL}")
    print(f"Embedding: {EMBEDDING_MODEL}")

    print("\nLoading embedding model...")
    embeddings = SentenceTransformerEmbeddings(EMBEDDING_MODEL)

    print("\nConnecting to Supabase vector store...")
    vector_store = SupabaseVectorStore()
    print("Supabase vector store connected.")

    retriever = Retriever(
        vector_store=vector_store,
        embeddings=embeddings
    )

    metrics = extract_financial_metrics(
        retriever=retriever,
        company=company,
        year=year
    )

    print_metrics(company=company, year=year, metrics=metrics)

    saved = False
    if save_to_postgres:
        saved = save_to_database(
            company=company, year=year, metrics=metrics
        )

    print("\n" + "=" * 80)
    print("RAG PIPELINE COMPLETED")
    print("=" * 80)
    print(f"PostgreSQL saved: {saved}")

    return {
        "company": company,
        "year": year,
        "metrics": metrics,
        "saved_to_postgres": saved
    }


# ============================================================
# TEST / DIRECT EXECUTION
# ============================================================

def main():
    company = os.getenv("TEST_COMPANY", "Apple")
    year = int(os.getenv("TEST_YEAR", "2024"))
    run_rag_pipeline(
        company=company,
        year=year,
        save_to_postgres=True
    )


if __name__ == "__main__":
    main()