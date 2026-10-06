from sqlalchemy import text

from database.postgres_sql import get_engine


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

        rows = [
            dict(row._mapping)
            for row in result
        ]

    return rows


if __name__ == "__main__":
    rows = get_metrics()

    print("=" * 80)
    print("FINANCIAL METRICS")
    print("=" * 80)

    for row in rows:
        print(row)
