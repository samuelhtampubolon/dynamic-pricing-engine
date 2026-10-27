"""OWASP API Security, JWT Token Lifecycle, BOLA Protection, and Rate Limiting."""

from __future__ import annotations
import time
from typing import Any, Dict, Optional
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware

from src.config import settings

# SlowAPI Limiter
limiter = Limiter(key_func=get_remote_address, default_limits=[settings.RATE_LIMIT_DEFAULT])

# HTTP Bearer scheme
http_bearer = HTTPBearer(auto_error=True)


def create_access_token(
    tenant_id: str,
    subject: str = "api_client",
    role: str = "pricing_analyst",
    expires_minutes: Optional[int] = None,
) -> str:
    """Generate tenant-scoped cryptographically signed JWT token."""
    expire_delta = (expires_minutes or settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES) * 60
    now = int(time.time())
    payload = {
        "sub": subject,
        "tenant_id": tenant_id,
        "role": role,
        "iat": now,
        "exp": now + expire_delta,
        "iss": "dynamic-pricing-engine",
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> Dict[str, Any]:
    """Decode and validate signature & expiration of JWT token."""
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            issuer="dynamic-pricing-engine",
            options={"require": ["exp", "iat", "sub", "tenant_id"]},
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="JWT token has expired. Request a new token via /api/v1/auth/token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as err:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid authentication token: {str(err)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_tenant(
    credentials: HTTPAuthorizationCredentials = Depends(http_bearer),
) -> str:
    """Extract and verify authenticated tenant_id from Bearer token."""
    token = credentials.credentials
    payload = decode_access_token(token)
    tenant_id = payload.get("tenant_id")
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token missing mandatory tenant_id claim.",
        )
    return str(tenant_id)


def enforce_bola_isolation(auth_tenant_id: str, target_tenant_id: str) -> None:
    """OWASP API1: Broken Object Level Authorization (BOLA) Guard.

    Strictly prohibits a tenant from querying or modifying resources
    belonging to another tenant.
    """
    if auth_tenant_id != target_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"BOLA Violation: Authenticated tenant '{auth_tenant_id}' cannot access resources of tenant '{target_tenant_id}'.",
        )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Enforce defense-in-depth OWASP HTTP response security headers."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response
