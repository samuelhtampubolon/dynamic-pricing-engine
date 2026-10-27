"""Pydantic v2 schemas enforcing OWASP API3 Mass Assignment Prevention and strict validation."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class StrictBaseModel(BaseModel):
    """Base model enforcing OWASP API3: extra='forbid'."""
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
    )


class HealthResponse(StrictBaseModel):
    status: str
    timestamp: str
    service: str
    version: str


class ReadinessResponse(StrictBaseModel):
    status: str
    lakehouse_ready: bool
    gold_tables_present: bool
    checked_at: str
    tables: Dict[str, int]


class TokenRequest(StrictBaseModel):
    tenant_id: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    client_secret: str = Field(..., min_length=6, max_length=128)
    role: str = Field(default="pricing_analyst", max_length=32)


class TokenResponse(StrictBaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    tenant_id: str


class PricingQuoteRequest(StrictBaseModel):
    tenant_id: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    sku_id: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    inventory_level: int = Field(..., ge=0, le=1000000)
    competitor_price: Optional[float] = Field(None, gt=0.0, le=1000000.0, allow_inf_nan=False)
    demand_surge_factor: float = Field(1.0, ge=0.1, le=10.0, allow_inf_nan=False)


class PricingQuoteResponse(StrictBaseModel):
    sku_id: str
    tenant_id: str
    recommended_price: float
    raw_optimal_price: float
    unit_cogs: float
    min_margin_pct: float
    margin_floor_price: float
    max_msrp_ceiling: float
    elasticity_epsilon: float
    r_squared: float
    guardrail_status: str
    expected_margin_usd: float
    expected_margin_pct: float
    inventory_level: int
    competitor_price: Optional[float]
    calculated_at: str


class BanditSimRequest(StrictBaseModel):
    tenant_id: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    sku_id: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    horizon: int = Field(500, gt=10, le=5000)  # OWASP API4: capped at 5000
    algorithm: str = Field("thompson_sampling", pattern=r"^(thompson_sampling|epsilon_greedy)$")
    epsilon: Optional[float] = Field(0.1, ge=0.0, le=1.0)
    candidate_prices: Optional[List[float]] = Field(None, min_length=2, max_length=10)


class BanditSimResponse(StrictBaseModel):
    tenant_id: str
    sku_id: str
    algorithm: str
    horizon: int
    baseline_revenue: float
    baseline_profit: float
    bandit_revenue: float
    bandit_profit: float
    cumulative_regret: float
    profit_uplift_pct: float
    revenue_uplift_pct: float
    arm_allocations: Dict[str, int]
    arm_estimated_values: Dict[str, float]
    step_history: Optional[List[Dict[str, Any]]] = None


class CohortRecord(StrictBaseModel):
    cohort_id: str
    tenant_id: str
    arpu_usd: float
    gross_margin_pct: float
    churn_rate: float
    cac_usd: float
    ltv_usd: float
    ltv_cac_ratio: float
    payback_months: float


class CohortEconomicsResponse(StrictBaseModel):
    tenant_id: str
    cohorts: List[CohortRecord]
    aggregate_ltv_to_cac: float
    aggregate_cac_usd: float
    aggregate_ltv_usd: float
    avg_payback_months: float
