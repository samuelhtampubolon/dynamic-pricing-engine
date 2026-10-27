"""FastAPI application entrypoint with OWASP defenses, rate limiting, and health probes."""

import logging
from datetime import datetime, timezone

from fastapi import Body, FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from src.api.routes_pricing import router as pricing_router
from src.config import settings
from src.lakehouse import lakehouse
from src.schemas import HealthResponse, ReadinessResponse, TokenRequest, TokenResponse
from src.security import (
    BodySizeLimitMiddleware,
    SecurityHeadersMiddleware,
    authenticate_client,
    create_access_token,
    limiter,
)

logger = logging.getLogger("pricing_engine.api")

_docs_enabled = settings.ENV != "production"

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Enterprise-grade Real-Time Dynamic Pricing & Unit Economics Engine with DuckDB Lakehouse.",
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

# Rate limiter (decorated routes use their own limits; everything else the default)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Return 422 without echoing submitted values (which may include secrets)."""
    errors = [
        {"loc": err.get("loc"), "msg": err.get("msg"), "type": err.get("type")}
        for err in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


@app.exception_handler(Exception)
async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})


# Middleware order: the last added runs first (outermost).
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,  # bearer tokens only, no cookies
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=600,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.MAX_REQUEST_BYTES)
app.add_middleware(SecurityHeadersMiddleware)


@app.get(
    "/healthz",
    response_model=HealthResponse,
    tags=["Probes"],
    summary="Liveness Probe",
)
@limiter.exempt
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
@limiter.exempt
def readiness_probe() -> ReadinessResponse:
    """Readiness probe checking DuckDB lakehouse connectivity and Gold tables."""
    try:
        counts = lakehouse.get_table_counts()
    except Exception:
        logger.exception("Readiness check failed")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Lakehouse storage unreachable.")
    has_gold = counts.get("gold_sku_elasticity", 0) > 0 and counts.get("gold_cohort_economics", 0) > 0
    return ReadinessResponse(
        status="ready" if has_gold else "degraded",
        lakehouse_ready=True,
        gold_tables_present=has_gold,
        checked_at=datetime.now(timezone.utc).isoformat(),
        tables=counts,
    )


@app.post(
    "/api/v1/auth/token",
    response_model=TokenResponse,
    tags=["Authentication"],
    summary="Issue tenant-scoped JWT token",
)
@limiter.limit(settings.RATE_LIMIT_AUTH)
def issue_access_token(request: Request, payload: TokenRequest = Body(...)) -> TokenResponse:
    """Verify tenant client credentials (scrypt, constant time) and issue a short-lived JWT.

    The role claim is taken from the server-side credential vault; clients cannot request it.
    """
    role = authenticate_client(payload.tenant_id, payload.client_secret.get_secret_value())
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid tenant credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(
        tenant_id=payload.tenant_id,
        subject=f"client_{payload.tenant_id}",
        role=role,
    )
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in_minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES,
        tenant_id=payload.tenant_id,
        role=role,
    )


# Include pricing & unit economics routes
app.include_router(pricing_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "src.api.main:app",
        host=settings.HOST,
        port=settings.API_PORT,
        reload=settings.DEBUG,
        server_header=False,
    )
