# check_metrics.py
import os
from dotenv import load_dotenv
from sqlalchemy import text
from database.postgres_sql import get_engine

load_dotenv()


def fmt(v):
    if v is None:
        return "—"
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return str(v)


with get_engine().connect() as conn:
    rows = conn.execute(text("""
        SELECT company, revenue, net_income, operating_income,
               cash_flow_from_operations AS cash_flow,
               total_assets, total_liabilities
        FROM financial_metrics
        ORDER BY company
    """)).fetchall()

header = (
    f"{'Company':12} "
    f"{'Revenue':>12} "
    f"{'NetInc':>10} "
    f"{'OpInc':>10} "
    f"{'CashFlow':>10} "
    f"{'Assets':>10} "
    f"{'Liab':>10}"
)

print()
print("=" * len(header))
print(header)
print("=" * len(header))

for row in rows:
    d = dict(row._mapping)
    print(
        f"{d['company']:12} "
        f"{fmt(d['revenue']):>12} "
        f"{fmt(d['net_income']):>10} "
        f"{fmt(d['operating_income']):>10} "
        f"{fmt(d['cash_flow']):>10} "
        f"{fmt(d['total_assets']):>10} "
        f"{fmt(d['total_liabilities']):>10}"
    )

print("=" * len(header))
print()
