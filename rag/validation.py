"""Sanity checks applied to extracted KPIs before anything is saved."""

from __future__ import annotations

from typing import Any

FIELDS = (
    "revenue", "net_income", "operating_income", "cash_flow",
    "total_assets", "total_liabilities",
)


def _num(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def run_checks(metrics: dict[str, Any]) -> list[dict]:
    """Return a list of failed checks: {check, field, message}."""
    failures: list[dict] = []
    revenue = _num(metrics.get("revenue"))
    net = _num(metrics.get("net_income"))
    assets = _num(metrics.get("total_assets"))
    liabilities = _num(metrics.get("total_liabilities"))
    equity = _num(metrics.get("total_equity"))
    cash = _num(metrics.get("cash_flow"))

    if revenue is not None and revenue < 0:
        failures.append({"check": "revenue_non_negative", "field": "revenue",
                         "message": "Revenue is negative; revenue is not reported as a negative total."})

    if revenue is not None and net is not None and net > revenue * 1.01:
        failures.append({"check": "net_income_within_revenue", "field": "net_income",
                         "message": "Net income exceeds revenue; likely the wrong row or unit."})

    if assets is not None and liabilities is not None and equity is not None:
        residual = abs(assets - (liabilities + equity))
        if residual > max(abs(assets) * 0.01, 0.01):
            failures.append({
                "check": "accounting_identity", "field": "total_assets",
                "message": (f"Assets {assets:,.0f} != liabilities {liabilities:,.0f} + equity "
                            f"{equity:,.0f} (off by {residual / max(assets, 1) * 100:.1f}%)."),
            })

    if revenue and assets and assets / revenue > 20:
        failures.append({"check": "unit_sanity", "field": "total_assets",
                         "message": "Assets are more than 20x revenue; check for a unit mismatch (x1,000 / x10^7)."})
    if revenue and cash and abs(cash) / revenue > 3:
        failures.append({"check": "unit_sanity", "field": "cash_flow",
                         "message": "Operating cash flow is more than 3x revenue; check the row or unit."})
    if revenue and liabilities and liabilities / revenue > 25:
        failures.append({"check": "unit_sanity", "field": "total_liabilities",
                         "message": "Liabilities are more than 25x revenue; check for a unit mismatch."})

    # Same-value duplication between distinct concepts (extraction shortcuts).
    if (_num(metrics.get("operating_income")) is not None and net is not None
            and _num(metrics.get("operating_income")) == net):
        failures.append({"check": "distinct_fields", "field": "operating_income",
                         "message": "Operating income equals net income exactly; one of them is likely copied, not extracted."})

    return failures


def summarize_nulls(metrics: dict[str, Any]) -> list[str]:
    """Human-readable reasons for missing numeric KPIs."""
    reasons = []
    for field in FIELDS:
        if _num(metrics.get(field)) is None:
            reason = (metrics.get(f"{field}_reason") or "not found in the report")
            reasons.append(f"{field}: {reason}")
    return reasons


def extraction_status(metrics: dict[str, Any], failures: list[dict]) -> str:
    numeric = [_num(metrics.get(f)) for f in FIELDS]
    present = sum(1 for v in numeric if v is not None)
    if present == 0:
        return "failed"
    if failures:
        return "needs_review"
    if present < len(FIELDS):
        return "partial"
    return "ok"
