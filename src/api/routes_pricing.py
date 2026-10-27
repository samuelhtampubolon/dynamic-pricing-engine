"""API Routes for Dynamic Pricing, Multi-Armed Bandit Simulation, and Unit Economics.

Every route resolves the tenant from the verified JWT (OWASP API1). Where a
request also names a tenant, it must match the token or the call fails with 403.
"""

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status

from src.lakehouse import lakehouse
from src.schemas import (
    IDENTIFIER_PATTERN,
    BanditSimRequest,
    BanditSimResponse,
    CohortEconomicsResponse,
    ElasticityDiagnosticsResponse,
    PricingQuoteRequest,
    PricingQuoteResponse,
    SkuCatalogResponse,
)
from src.security import TENANT_ID_PATTERN, Principal, enforce_bola_isolation, get_current_principal, limiter
from src.services import pricing_math as pm
from src.services.bandit_simulator import bandit_service
from src.services.elasticity_model import SkuNotFoundError, elasticity_service
from src.services.unit_economics import unit_economics_service

logger = logging.getLogger("pricing_engine.api")

router = APIRouter(prefix="/api/v1", tags=["Pricing & Unit Economics"])


def _not_found(err: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(err))


def _internal(context: str) -> HTTPException:
    logger.exception("%s failed", context)
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"{context} failed.")


@router.get(
    "/catalog",
    response_model=SkuCatalogResponse,
    summary="List the authenticated tenant's SKU catalog",
)
@limiter.limit("120/minute")
def get_catalog(request: Request, principal: Principal = Depends(get_current_principal)) -> SkuCatalogResponse:
    skus = lakehouse.get_sku_catalog(principal.tenant_id)
    for s in skus:
        s["margin_floor_price"] = round(pm.margin_floor_price(s["unit_cogs"], s["min_margin_pct"]), 2)
    return SkuCatalogResponse(tenant_id=principal.tenant_id, skus=skus)


@router.post(
    "/pricing/quote",
    response_model=PricingQuoteResponse,
    summary="Compute real-time guarded price quote",
    description="Calculates log-log optimal price clamped to margin floor and MSRP ceiling in <50ms.",
)
@limiter.limit("120/minute")
def compute_pricing_quote(
    request: Request,
    payload: PricingQuoteRequest = Body(...),
    principal: Principal = Depends(get_current_principal),
) -> PricingQuoteResponse:
    """OWASP API1 BOLA Enforcement: Verify caller's tenant matches payload tenant."""
    enforce_bola_isolation(principal.tenant_id, payload.tenant_id)
    try:
        quote = elasticity_service.compute_guarded_quote(
            tenant_id=principal.tenant_id,
            sku_id=payload.sku_id,
            inventory_level=payload.inventory_level,
            competitor_price=payload.competitor_price,
            demand_surge_factor=payload.demand_surge_factor,
        )
        return PricingQuoteResponse(**quote)
    except SkuNotFoundError as err:
        raise _not_found(err)
    except pm.GuardrailConfigError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="SKU guardrails are inconsistent.")
    except Exception:
        raise _internal("Pricing inference")


@router.get(
    "/pricing/elasticity",
    response_model=ElasticityDiagnosticsResponse,
    summary="Log-log OLS elasticity estimate with full regression diagnostics",
)
@limiter.limit("60/minute")
def get_elasticity_diagnostics(
    request: Request,
    sku_id: str = Query(..., min_length=2, max_length=64, pattern=IDENTIFIER_PATTERN),
    principal: Principal = Depends(get_current_principal),
) -> ElasticityDiagnosticsResponse:
    try:
        fit = elasticity_service.fit_sku_elasticity(principal.tenant_id, sku_id)
    except SkuNotFoundError as err:
        raise _not_found(err)
    except Exception:
        raise _internal("Elasticity estimation")
    fields = ElasticityDiagnosticsResponse.model_fields.keys()
    return ElasticityDiagnosticsResponse(**{k: fit[k] for k in fields})


@router.post(
    "/simulation/bandit",
    response_model=BanditSimResponse,
    summary="Run Thompson Sampling, UCB1 or Epsilon-Greedy pricing experiment",
    description="Simulates dynamic pricing experimentation against a static baseline for up to 5000 rounds.",
)
@limiter.limit("60/minute")
def run_bandit_simulation(
    request: Request,
    payload: BanditSimRequest = Body(...),
    principal: Principal = Depends(get_current_principal),
) -> BanditSimResponse:
    """OWASP API1 BOLA Enforcement: Verify caller's tenant matches payload tenant."""
    enforce_bola_isolation(principal.tenant_id, payload.tenant_id)
    try:
        sim_res = bandit_service.run_simulation(
            tenant_id=principal.tenant_id,
            sku_id=payload.sku_id,
            horizon=payload.horizon,
            algorithm=payload.algorithm,
            epsilon_param=payload.epsilon if payload.epsilon is not None else 0.1,
            candidate_prices=payload.candidate_prices,
            seed=payload.seed,
            include_history=payload.include_history,
        )
        return BanditSimResponse(**sim_res)
    except SkuNotFoundError as err:
        raise _not_found(err)
    except Exception:
        raise _internal("Bandit simulation")


@router.get(
    "/economics/cohorts",
    response_model=CohortEconomicsResponse,
    summary="Fetch tenant-isolated cohort economics",
    description="Returns ARPU, gross margin, churn, CAC, LTV (simple and discounted) and payback per monthly cohort.",
)
@limiter.limit("120/minute")
def get_cohort_economics(
    request: Request,
    tenant_id: str = Query(..., min_length=2, max_length=64, pattern=TENANT_ID_PATTERN),
    principal: Principal = Depends(get_current_principal),
) -> CohortEconomicsResponse:
    """OWASP API1 BOLA Enforcement: Caller can only retrieve their own tenant cohorts."""
    enforce_bola_isolation(principal.tenant_id, tenant_id)
    try:
        return CohortEconomicsResponse(**unit_economics_service.get_tenant_cohorts(principal.tenant_id))
    except Exception:
        raise _internal("Cohort economics")
