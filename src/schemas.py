"""Pydantic v2 schemas enforcing OWASP API3 Mass Assignment Prevention and strict validation."""

from typing import Annotated, Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from src.security import TENANT_ID_PATTERN

IDENTIFIER_PATTERN = r"^[a-zA-Z0-9_\-]{2,64}$"


class StrictBaseModel(BaseModel):
    """Base model enforcing OWASP API3: extra='forbid' and no NaN/Inf anywhere."""
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )


BoundedPrice = Annotated[float, Field(gt=0.0, le=1_000_000.0, allow_inf_nan=False)]


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
    """The role is assigned server-side from the credential vault, never by the client."""
    tenant_id: str = Field(..., min_length=2, max_length=64, pattern=TENANT_ID_PATTERN)
    client_secret: SecretStr = Field(..., min_length=12, max_length=128)


class TokenResponse(StrictBaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    tenant_id: str
    role: str


class PricingQuoteRequest(StrictBaseModel):
    tenant_id: str = Field(..., min_length=2, max_length=64, pattern=TENANT_ID_PATTERN)
    sku_id: str = Field(..., min_length=2, max_length=64, pattern=IDENTIFIER_PATTERN)
    inventory_level: int = Field(..., ge=0, le=1_000_000)
    competitor_price: Optional[BoundedPrice] = None
    demand_surge_factor: float = Field(1.0, ge=0.1, le=10.0, allow_inf_nan=False)


class PricingWaterfallStep(StrictBaseModel):
    stage: str
    price: float
    formula: str


class PricingQuoteResponse(StrictBaseModel):
    sku_id: str
    tenant_id: str
    recommended_price: float
    raw_optimal_price: float
    anchored_price: float
    inventory_adjusted_price: float
    base_price: float
    unit_cogs: float
    min_margin_pct: float
    margin_floor_price: float
    floor_gross_margin_pct: float
    max_msrp_ceiling: float
    elasticity_epsilon: float
    cross_elasticity_beta: float
    r_squared: float
    optimization_regime: str
    inventory_regime: str
    guardrail_status: str
    lerner_index: float
    theoretical_lerner_index: float
    markup_on_cogs_pct: float
    expected_margin_usd: float
    expected_margin_pct: float
    expected_daily_units: float
    expected_daily_profit_usd: float
    days_of_cover: Optional[float]
    expected_profit_uplift_pct: float
    breakeven_volume_change_pct: Optional[float]
    inventory_level: int
    competitor_price: Optional[float]
    demand_surge_factor: float
    pricing_waterfall: List[PricingWaterfallStep]
    calculated_at: str


class ElasticityDiagnosticsResponse(StrictBaseModel):
    sku_id: str
    tenant_id: str
    elasticity_epsilon: float
    cross_elasticity_beta: float
    alpha: float
    r_squared: float
    adj_r_squared: float
    rmse_log: float
    std_error_epsilon: float
    t_stat_epsilon: float
    p_value_epsilon: float
    ci95_epsilon_low: float
    ci95_epsilon_high: float
    std_error_beta: float
    durbin_watson: float
    vif_log_price: float
    smearing_factor: float
    aic: float
    bic: float
    sample_size: int
    is_prior_fallback: bool
    optimization_regime: str
    raw_optimal_price: float
    theoretical_lerner_index: float


class BanditSimRequest(StrictBaseModel):
    tenant_id: str = Field(..., min_length=2, max_length=64, pattern=TENANT_ID_PATTERN)
    sku_id: str = Field(..., min_length=2, max_length=64, pattern=IDENTIFIER_PATTERN)
    horizon: int = Field(500, gt=10, le=5000)  # OWASP API4: capped at 5000
    algorithm: Literal["thompson_sampling", "epsilon_greedy", "ucb1"] = "thompson_sampling"
    epsilon: Optional[float] = Field(0.1, ge=0.0, le=1.0, allow_inf_nan=False)
    candidate_prices: Optional[List[BoundedPrice]] = Field(None, min_length=2, max_length=10)
    seed: Optional[int] = Field(None, ge=0, le=2**32 - 1)
    include_history: bool = True


class BanditSimResponse(StrictBaseModel):
    tenant_id: str
    sku_id: str
    algorithm: str
    horizon: int
    baseline_price: float
    baseline_revenue: float
    baseline_profit: float
    bandit_revenue: float
    bandit_profit: float
    cumulative_regret: float
    static_cumulative_regret: float
    regret_reduction_pct: float
    profit_uplift_pct: float
    revenue_uplift_pct: float
    optimal_arm_price: float
    optimal_arm_share: float
    arm_allocations: Dict[str, int]
    arm_estimated_values: Dict[str, float]
    arm_true_expected_profit: Dict[str, float]
    step_history: Optional[List[Dict[str, Any]]] = None


class CohortRecord(StrictBaseModel):
    cohort_id: str
    tenant_id: str
    new_customers: int
    arpu_usd: float
    gross_margin_pct: float
    churn_rate: float
    cac_usd: float
    ltv_usd: float
    ltv_cac_ratio: float
    payback_months: float
    monthly_contribution_usd: float
    discounted_ltv_usd: float
    discounted_ltv_cac_ratio: float
    churn_adjusted_payback_months: Optional[float]
    expected_lifetime_months: float
    annual_churn_rate: float
    customer_half_life_months: float


class CohortEconomicsResponse(StrictBaseModel):
    tenant_id: str
    cohorts: List[CohortRecord]
    aggregate_ltv_to_cac: float
    aggregate_cac_usd: float
    aggregate_ltv_usd: float
    aggregate_discounted_ltv_usd: float
    aggregate_discounted_ltv_to_cac: float
    avg_payback_months: float
    total_new_customers: int
    annual_discount_rate: float


class SkuCatalogItem(StrictBaseModel):
    sku_id: str
    tenant_id: str
    sku_name: str
    unit_cogs: float
    min_margin_pct: float
    max_msrp: float
    base_price: float
    margin_floor_price: float


class SkuCatalogResponse(StrictBaseModel):
    tenant_id: str
    skus: List[SkuCatalogItem]
