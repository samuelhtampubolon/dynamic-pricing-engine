"""API Routes for Dynamic Pricing, Multi-Armed Bandit Simulation, and Unit Economics."""

from typing import Any, Dict
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from slowapi import Limiter

from src.config import settings
from src.lakehouse import lakehouse
from src.schemas import (
    BanditSimRequest,
    BanditSimResponse,
    CohortEconomicsResponse,
    PricingQuoteRequest,
    PricingQuoteResponse,
)
from src.security import enforce_bola_isolation, get_current_tenant, limiter
from src.services.bandit_simulator import bandit_service
from src.services.elasticity_model import elasticity_service
from src.services.unit_economics import unit_economics_service

router = APIRouter(prefix="/api/v1", tags=["Pricing & Unit Economics"])


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
    authenticated_tenant: str = Depends(get_current_tenant),
) -> PricingQuoteResponse:
    """OWASP API1 BOLA Enforcement: Verify caller's tenant matches payload tenant."""
    enforce_bola_isolation(authenticated_tenant, payload.tenant_id)

    try:
        quote = elasticity_service.compute_guarded_quote(
            tenant_id=payload.tenant_id,
            sku_id=payload.sku_id,
            inventory_level=payload.inventory_level,
            competitor_price=payload.competitor_price,
            demand_surge_factor=payload.demand_surge_factor,
        )
        return PricingQuoteResponse(**quote)
    except ValueError as val_err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(val_err))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Inference failure: {str(exc)}",
        )


@router.post(
    "/simulation/bandit",
    response_model=BanditSimResponse,
    summary="Run Thompson Sampling or Epsilon-Greedy pricing experiment",
    description="Simulates dynamic pricing experimentation against a static baseline for up to 5000 rounds.",
)
@limiter.limit("60/minute")
def run_bandit_simulation(
    request: Request,
    payload: BanditSimRequest = Body(...),
    authenticated_tenant: str = Depends(get_current_tenant),
) -> BanditSimResponse:
    """OWASP API1 BOLA Enforcement: Verify caller's tenant matches payload tenant."""
    enforce_bola_isolation(authenticated_tenant, payload.tenant_id)

    try:
        sim_res = bandit_service.run_simulation(
            tenant_id=payload.tenant_id,
            sku_id=payload.sku_id,
            horizon=payload.horizon,
            algorithm=payload.algorithm,
            epsilon_param=payload.epsilon or 0.1,
            candidate_prices=payload.candidate_prices,
        )
        return BanditSimResponse(**sim_res)
    except ValueError as val_err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(val_err))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Simulation failure: {str(exc)}",
        )


@router.get(
    "/economics/cohorts",
    response_model=CohortEconomicsResponse,
    summary="Fetch tenant-isolated cohort economics",
    description="Returns ARPU, gross margin, churn, CAC, LTV, and payback months per monthly cohort.",
)
@limiter.limit("120/minute")
def get_cohort_economics(
    request: Request,
    tenant_id: str = Query(..., min_length=2, max_length=64),
    authenticated_tenant: str = Depends(get_current_tenant),
) -> CohortEconomicsResponse:
    """OWASP API1 BOLA Enforcement: Caller can only retrieve their own tenant cohorts."""
    enforce_bola_isolation(authenticated_tenant, tenant_id)

    try:
        cohort_data = unit_economics_service.get_tenant_cohorts(tenant_id)
        return CohortEconomicsResponse(**cohort_data)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve cohort economics: {str(exc)}",
        )
