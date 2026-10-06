from pydantic import BaseModel
from typing import List, Optional


class FinancialMetrics(BaseModel):
    company: str
    year: int

    revenue: Optional[float] = None
    net_income: Optional[float] = None
    operating_income: Optional[float] = None
    cash_flow: Optional[float] = None
    total_assets: Optional[float] = None
    total_liabilities: Optional[float] = None

    risk_factors: List[str] = []
    growth_drivers: List[str] = []


class MetricsResponse(BaseModel):
    success: bool
    data: FinancialMetrics