from decimal import Decimal
from sqlalchemy import text

from database.postgres_sql import get_engine


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

    query = """
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

    query = """
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


if __name__ == "__main__":
    print("=" * 80)
    print("FINANCIAL METRICS")
    print("=" * 80)

    rows = get_metrics()

    for row in rows:
        print(row)
