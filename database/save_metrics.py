import os
from typing import Any

from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()


def get_supabase_client() -> Client:
    """Create and return a Supabase client."""

    url = os.getenv("SUPABASE_URL")
    api_key = os.getenv("SUPABASE_API_KEY")

    if not url:
        raise RuntimeError(
            "Missing SUPABASE_URL in .env"
        )

    if not api_key:
        raise RuntimeError(
            "Missing SUPABASE_API_KEY in .env"
        )

    return create_client(
        url,
        api_key
    )


def save_metrics(
    company: str,
    year: int,
    metrics: dict[str, Any]
) -> None:
    """
    Save extracted financial metrics to Supabase PostgreSQL.

    If the same company/year already exists,
    the existing record is updated.
    """

    client = get_supabase_client()

    # --------------------------------------------------------
    # Support both capitalized and lowercase keys
    # --------------------------------------------------------

    revenue = (
        metrics.get("Revenue")
        if metrics.get("Revenue") is not None
        else metrics.get("revenue")
    )

    net_income = (
        metrics.get("Net Income")
        if metrics.get("Net Income") is not None
        else metrics.get("net_income")
    )

    operating_income = (
        metrics.get("Operating Income")
        if metrics.get("Operating Income") is not None
        else metrics.get("operating_income")
    )

    cash_flow = (
        metrics.get("Cash Flow from Operating Activities")
        if metrics.get("Cash Flow from Operating Activities") is not None
        else metrics.get("cash_flow")
    )

    total_assets = (
        metrics.get("Total Assets")
        if metrics.get("Total Assets") is not None
        else metrics.get("total_assets")
    )

    total_liabilities = (
        metrics.get("Total Liabilities")
        if metrics.get("Total Liabilities") is not None
        else metrics.get("total_liabilities")
    )

    risk_factors = (
        metrics.get("Top Risk Factors")
        if metrics.get("Top Risk Factors") is not None
        else metrics.get("risk_factors")
    )

    growth_drivers = (
        metrics.get("Top Growth Drivers")
        if metrics.get("Top Growth Drivers") is not None
        else metrics.get("growth_drivers")
    )

    # --------------------------------------------------------
    # Make sure JSONB fields are lists
    # --------------------------------------------------------

    if risk_factors is None:
        risk_factors = []

    if isinstance(risk_factors, str):
        risk_factors = [risk_factors]

    if growth_drivers is None:
        growth_drivers = []

    if isinstance(growth_drivers, str):
        growth_drivers = [growth_drivers]

    # --------------------------------------------------------
    # Prepare database record
    # --------------------------------------------------------

    record = {
        "company": company,
        "year": int(year),

        "revenue": revenue,
        "net_income": net_income,
        "operating_income": operating_income,

        "cash_flow_from_operations": cash_flow,

        "total_assets": total_assets,
        "total_liabilities": total_liabilities,

        "risk_factors": risk_factors,
        "growth_drivers": growth_drivers,
    }

    print()
    print("=" * 80)
    print("SAVING FINANCIAL METRICS")
    print("=" * 80)

    print(f"Company : {company}")
    print(f"Year    : {year}")

    # --------------------------------------------------------
    # Upsert into Supabase
    # --------------------------------------------------------

    response = (
        client
        .table("financial_metrics")
        .upsert(
            record,
            on_conflict="company,year"
        )
        .execute()
    )

    if not response.data:
        raise RuntimeError(
            "Supabase did not return the saved record."
        )

    print()
    print(
        "Financial metrics saved successfully."
    )

    print("=" * 80)


# ============================================================
# TEST
# ============================================================

if __name__ == "__main__":

    sample_metrics = {

        "Revenue": 391035,

        "Net Income": 93736,

        "Operating Income": 123216,

        "Cash Flow from Operating Activities": 118254,

        "Total Assets": 364980,

        "Total Liabilities": 308030,

        "Top Risk Factors": [
            "Interest rate risk affecting investment portfolio and term debt.",
            "Potential constraints on cash requirements due to debt market conditions."
        ],

        "Top Growth Drivers": [
            "iPhone sales (stable).",
            "Mac sales (increase).",
            "Services sales (increase)."
        ]
    }

    save_metrics(
        company="Apple",
        year=2024,
        metrics=sample_metrics
    )
