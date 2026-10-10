from decimal import Decimal
from sqlalchemy import text

from database.postgres_sql import get_engine

NEW_COLUMNS = (
    "currency", "unit", "unit_multiplier", "period_start", "period_end",
    "basis", "extraction_status", "extraction_notes", "metric_sources",
    "source_file", "source_type", "source_url", "source_retrieved_at",
    "updated_at",
)


def normalize_value(value):
    """
    Convert PostgreSQL numeric/decimal/string values into
    JSON-friendly numeric values.
    """
    if value is None:
        return None

    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)

    if isinstance(value, str):
        value = value.strip()

        if not value:
            return None

        try:
            number = Decimal(value)
            return int(number) if number == number.to_integral_value() else float(number)
        except Exception:
            return value

    return value


def normalize_row(row):
    """
    Convert a SQLAlchemy row into a frontend/API-friendly dictionary.
    """
    data = dict(row._mapping)

    numeric_fields = [
        "revenue",
        "net_income",
        "operating_income",
        "cash_flow",
        "total_assets",
        "total_liabilities",
    ]

    for field in numeric_fields:
        data[field] = normalize_value(data.get(field))

    for field in ("metric_sources", "extraction_notes"):
        if isinstance(data.get(field), str):
            import json
            try:
                data[field] = json.loads(data[field])
            except ValueError:
                data[field] = {}

    if str(data.get('year')) == '2023':
        import json
        from services.reports import ROOT
        try:
            provenance = json.loads((ROOT/'config/prior_year_sources.json').read_text(encoding='utf-8'))
            data['metric_sources'] = provenance.get(data.get('company'), {})
        except (OSError, ValueError):
            data['metric_sources'] = {}

    return data


def get_metrics():
    engine = get_engine()

    query = f"""
    SELECT
        id,
        company,
        year,
        revenue,
        net_income,
        operating_income,
        cash_flow_from_operations AS cash_flow,
        total_assets,
        total_liabilities,
        risk_factors,
        growth_drivers,
        {', '.join(NEW_COLUMNS)},
        created_at
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY company, year
                   ORDER BY created_at DESC
               ) AS rn
        FROM financial_metrics
    ) t
    WHERE rn = 1
    ORDER BY company;
    """

    with engine.connect() as connection:
        result = connection.execute(text(query))
        rows = [normalize_row(row) for row in result]

    return rows


def get_metrics_by_company_year(company: str, year: int):
    engine = get_engine()

    query = f"""
    SELECT
        id,
        company,
        year,
        revenue,
        net_income,
        operating_income,
        cash_flow_from_operations AS cash_flow,
        total_assets,
        total_liabilities,
        risk_factors,
        growth_drivers,
        {', '.join(NEW_COLUMNS)},
        created_at
    FROM financial_metrics
    WHERE LOWER(company) = LOWER(:company)
      AND year = :year
    ORDER BY created_at DESC
    LIMIT 1;
    """

    with engine.connect() as connection:
        result = connection.execute(
            text(query),
            {
                "company": company,
                "year": str(year),
            }
        )

        row = result.fetchone()

        if row is None:
            return None

        return normalize_row(row)


def get_metrics_rows_for_company(company: str) -> list[dict]:
    """All stored rows for one company across years (for period matching)."""
    engine = get_engine()
    query = f"""
    SELECT
        id, company, year,
        revenue, net_income, operating_income,
        cash_flow_from_operations AS cash_flow,
        total_assets, total_liabilities,
        risk_factors, growth_drivers,
        {', '.join(NEW_COLUMNS)},
        created_at
    FROM financial_metrics
    WHERE LOWER(company) = LOWER(:company)
    ORDER BY year DESC;
    """
    with engine.connect() as connection:
        result = connection.execute(text(query), {"company": company})
        return [normalize_row(row) for row in result]


def get_company_catalog() -> list[dict]:
    """One entry per company with its stored report versions and data quality.

    The frontend builds the sidebar, year selector, comparisons and charts
    from this endpoint alone."""
    engine = get_engine()
    query = """
    SELECT company, year, version, currency, unit, basis, period_start, period_end,
           extraction_status, extraction_notes, metric_sources, source_file,
           source_type, updated_at,
           revenue, net_income, operating_income, cash_flow_from_operations,
           total_assets, total_liabilities
    FROM financial_metrics
    ORDER BY lower(company), year, version
    """
    with engine.connect() as connection:
        rows = [dict(row._mapping) for row in connection.execute(text(query))]

    companies: dict[str, dict] = {}
    for row in rows:
        name = row["company"]
        entry = companies.setdefault(name, {"company": name, "years": []})
        numeric = [row.get(f) for f in (
            "revenue", "net_income", "operating_income",
            "cash_flow_from_operations", "total_assets", "total_liabilities")]
        present = sum(1 for v in numeric if v is not None)
        sources = row.get("metric_sources")
        if isinstance(sources, str):
            try:
                import json
                sources = json.loads(sources)
            except ValueError:
                sources = {}
        sources = sources if isinstance(sources, dict) else {}
        entry["years"].append({
            "year": int(row["year"]) if row["year"] is not None else None,
            "version": row.get("version", 1),
            "currency": row.get("currency"),
            "unit": row.get("unit"),
            "basis": row.get("basis"),
            "period_start": str(row["period_start"]) if row.get("period_start") else None,
            "period_end": str(row["period_end"]) if row.get("period_end") else None,
            "extraction_status": row.get("extraction_status"),
            "source_file": row.get("source_file"),
            "source_type": row.get("source_type"),
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
            "data_quality": {
                "metrics_present": present,
                "metrics_total": 6,
                "missing": [name_ for name_, v in zip(
                    ("revenue", "net_income", "operating_income", "cash_flow",
                     "total_assets", "total_liabilities"), numeric) if v is None],
                "needs_review": row.get("extraction_status") in ("needs_review", "partial"),
                "manual_overrides": sum(
                    1 for s in sources.values()
                    if isinstance(s, dict) and s.get("status") == "manual_override"),
            },
        })
    return list(companies.values())


if __name__ == "__main__":
    print("=" * 80)
    print("FINANCIAL METRICS")
    print("=" * 80)

    rows = get_metrics()

    for row in rows:
        print(row)
