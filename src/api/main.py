"""FastAPI application entrypoint with OWASP defenses, rate limiting, and health probes."""

from datetime import datetime, timezone
from fastapi import Body, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

from src.api.routes_pricing import router as pricing_router
from src.config import settings
from src.lakehouse import lakehouse
from src.schemas import HealthResponse, ReadinessResponse, TokenRequest, TokenResponse
from src.security import SecurityHeadersMiddleware, create_access_token, limiter

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Enterprise-grade Real-Time Dynamic Pricing & Unit Economics Engine with DuckDB Lakehouse.",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# Attach rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# OWASP Security Headers & CORS
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get(
    "/healthz",
    response_model=HealthResponse,
    tags=["Probes"],
    summary="Liveness Probe",
)
def health_probe() -> HealthResponse:
    """Liveness probe verifying that the API worker is running."""
    return HealthResponse(
        status="healthy",
        timestamp=datetime.now(timezone.utc).isoformat(),
        service=settings.PROJECT_NAME,
        version=settings.VERSION,
    )


@app.get(
    "/readyz",
    response_model=ReadinessResponse,
    tags=["Probes"],
    summary="Readiness Probe",
)
def readiness_probe() -> ReadinessResponse:
    """Readiness probe checking DuckDB lakehouse connectivity and Gold tables."""
    try:
        counts = lakehouse.get_table_counts()
        has_gold = (
            counts.get("gold_sku_elasticity", 0) > 0
            and counts.get("gold_cohort_economics", 0) > 0
        )
        return ReadinessResponse(
            status="ready" if has_gold else "degraded",
            lakehouse_ready=True,
            gold_tables_present=has_gold,
            checked_at=datetime.now(timezone.utc).isoformat(),
            tables=counts,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Lakehouse storage unreachable: {str(exc)}",
        )


@app.post(
    "/api/v1/auth/token",
    response_model=TokenResponse,
    tags=["Authentication"],
    summary="Issue tenant-scoped JWT token",
)
@limiter.limit("30/minute")
def issue_access_token(request: Request, payload: TokenRequest = Body(...)) -> TokenResponse:
    """Issues a cryptographically signed tenant-scoped JWT access token."""
    # In a production environment, verify client_secret against hashed tenant credential vault
    token = create_access_token(
        tenant_id=payload.tenant_id,
        subject=f"client_{payload.tenant_id}",
        role=payload.role,
    )
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in_minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES,
        tenant_id=payload.tenant_id,
    )


# Include pricing & unit economics routes
app.include_router(pricing_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.api.main:app", host=settings.HOST, port=settings.API_PORT, reload=settings.DEBUG)
