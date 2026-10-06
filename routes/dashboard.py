from fastapi import APIRouter
from sqlalchemy import text

from database.postgres_sql import get_engine

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
def get_metrics():
    """
    Latest metric row per (company, year).
    """
    engine = get_engine()

    query = """
    SELECT company, year,
           revenue, net_income, operating_income, cash_flow,
           total_assets, total_liabilities,
           risk_factors, growth_drivers,
           created_at, updated_at
    FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY company, year
                   ORDER BY created_at DESC
               ) AS rn
        FROM financial_metrics
    ) t
    WHERE rn = 1
    ORDER BY company, year DESC
    """

    with engine.connect() as connection:
        result = connection.execute(text(query))
        rows = [dict(row._mapping) for row in result]

    return rows