"""OWASP API Security: credential verification, JWT lifecycle, BOLA guard, rate limiting
and defense-in-depth ASGI middlewares.

Run ``python -m src.credentials hash-secret`` to produce a scrypt hash for the
``TENANT_CREDENTIALS`` setting.
"""

from __future__ import annotations

import hmac
import logging
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.config import settings
from src.credentials import hash_client_secret, is_valid_hash, verify_client_secret

audit_log = logging.getLogger("pricing_engine.security")

# SlowAPI Limiter (OWASP API4)
limiter = Limiter(key_func=get_remote_address, default_limits=[settings.RATE_LIMIT_DEFAULT])

# HTTP Bearer scheme (auto_error=False so we can return a uniform 401)
http_bearer = HTTPBearer(auto_error=False)

TENANT_ID_PATTERN = r"^[a-zA-Z0-9_\-]{2,64}$"
_TENANT_RE = re.compile(TENANT_ID_PATTERN)
ALLOWED_ROLES = frozenset({"pricing_analyst", "growth_manager", "revenue_admin"})

# ---------------------------------------------------------------------------
# Credential vault (scrypt, constant-time verification)
# ---------------------------------------------------------------------------
# Local-only demo credentials, used when TENANT_CREDENTIALS is unset outside production.
DEMO_TENANT_SECRETS: Dict[str, str] = {
    "tenant_alpha": "alpha-local-demo-secret",
    "tenant_beta": "beta-local-demo-secret",
}


@dataclass(frozen=True)
class _CredentialRecord:
    role: str
    secret_hash: str


def _load_credential_vault() -> Dict[str, _CredentialRecord]:
    raw = settings.TENANT_CREDENTIALS.get_secret_value() if settings.TENANT_CREDENTIALS else ""
    vault: Dict[str, _CredentialRecord] = {}
    if raw.strip():
        for entry in raw.split(","):
            entry = entry.strip()
            if not entry:
                continue
            tenant_id, role, secret_hash = (entry.split(":", 2) + ["", ""])[:3]
            if not _TENANT_RE.match(tenant_id) or role not in ALLOWED_ROLES or not is_valid_hash(secret_hash):
                raise ValueError(f"Invalid TENANT_CREDENTIALS entry for tenant '{tenant_id}'.")
            vault[tenant_id] = _CredentialRecord(role=role, secret_hash=secret_hash)
        return vault
    if settings.ENV == "production":  # also enforced by Settings, kept as defense in depth
        raise RuntimeError("TENANT_CREDENTIALS must be configured in production.")
    audit_log.warning("TENANT_CREDENTIALS not set: using local demo credentials (development only).")
    return {
        tenant: _CredentialRecord(role="pricing_analyst", secret_hash=hash_client_secret(secret))
        for tenant, secret in DEMO_TENANT_SECRETS.items()
    }


_VAULT: Dict[str, _CredentialRecord] = _load_credential_vault()
# Verifying against a dummy hash for unknown tenants keeps response timing uniform
# and prevents tenant enumeration.
_DUMMY_HASH = hash_client_secret(secrets.token_urlsafe(16))


def authenticate_client(tenant_id: str, client_secret: str) -> Optional[str]:
    """Return the server-assigned role if the credentials are valid, else ``None``."""
    record = _VAULT.get(tenant_id)
    ok = verify_client_secret(client_secret, record.secret_hash if record else _DUMMY_HASH)
    if record is None or not ok:
        audit_log.warning("auth.failed tenant=%s", tenant_id)
        return None
    return record.role


# ---------------------------------------------------------------------------
# JWT lifecycle
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Principal:
    tenant_id: str
    role: str
    subject: str
    token_id: str


def create_access_token(
    tenant_id: str,
    subject: str = "api_client",
    role: str = "pricing_analyst",
    expires_minutes: Optional[int] = None,
) -> str:
    """Generate a tenant-scoped, signed, short-lived JWT."""
    if not _TENANT_RE.match(tenant_id):
        raise ValueError("Invalid tenant_id.")
    if role not in ALLOWED_ROLES:
        raise ValueError("Invalid role.")
    expire_delta = (expires_minutes or settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES) * 60
    now = int(time.time())
    payload = {
        "sub": subject,
        "tenant_id": tenant_id,
        "role": role,
        "iat": now,
        "nbf": now,
        "exp": now + expire_delta,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.JWT_ALGORITHM)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def decode_access_token(token: str) -> Dict[str, Any]:
    """Validate signature, algorithm, issuer, audience and time claims of a JWT."""
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.JWT_ALGORITHM],  # pinned: rejects 'none' and alg confusion
            issuer=settings.JWT_ISSUER,
            audience=settings.JWT_AUDIENCE,
            leeway=10,
            options={"require": ["exp", "iat", "nbf", "iss", "aud", "sub", "tenant_id", "jti"]},
        )
    except jwt.ExpiredSignatureError:
        raise _unauthorized("JWT token has expired. Request a new token via /api/v1/auth/token.")
    except jwt.InvalidTokenError:
        # Do not echo parser internals back to the caller.
        raise _unauthorized("Invalid authentication token.")


def get_current_principal(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(http_bearer),
) -> Principal:
    """Resolve the authenticated principal from the Bearer token."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized("Not authenticated.")
    payload = decode_access_token(credentials.credentials)
    tenant_id = str(payload.get("tenant_id", ""))
    role = str(payload.get("role", ""))
    if not _TENANT_RE.match(tenant_id) or role not in ALLOWED_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token claims are not authorized.")
    return Principal(tenant_id=tenant_id, role=role, subject=str(payload["sub"]), token_id=str(payload["jti"]))


def get_current_tenant(principal: Principal = Depends(get_current_principal)) -> str:
    """Backward compatible dependency returning only the authenticated tenant_id."""
    return principal.tenant_id


def enforce_bola_isolation(auth_tenant_id: str, target_tenant_id: str) -> None:
    """OWASP API1: Broken Object Level Authorization (BOLA) Guard.

    Strictly prohibits a tenant from querying resources belonging to another
    tenant. The response does not echo tenant identifiers, so it cannot be used
    to enumerate other tenants.
    """
    if not hmac.compare_digest(auth_tenant_id.encode(), target_tenant_id.encode()):
        audit_log.warning("bola.denied auth_tenant=%s target_tenant=%s", auth_tenant_id, target_tenant_id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="BOLA Violation: token is not authorized for the requested tenant scope.",
        )


# ---------------------------------------------------------------------------
# ASGI middlewares (pure ASGI: no BaseHTTPMiddleware body buffering)
# ---------------------------------------------------------------------------
_API_CSP = (
    "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
    "form-action 'self'; object-src 'none'"
)
# Swagger UI / ReDoc load their bundles from jsDelivr and bootstrap with an inline script.
_DOCS_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https://fastapi.tiangolo.com; "
    "worker-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
)
_DOCS_PATHS = ("/docs", "/redoc")


class SecurityHeadersMiddleware:
    """Inject defense-in-depth OWASP HTTP response security headers."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        request_id = uuid.uuid4().hex

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Content-Security-Policy"] = _DOCS_CSP if path.startswith(_DOCS_PATHS) else _API_CSP
                headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
                headers["Cross-Origin-Opener-Policy"] = "same-origin"
                headers["Cross-Origin-Resource-Policy"] = "same-origin"
                headers["X-Permitted-Cross-Domain-Policies"] = "none"
                # Legacy XSS auditor is deprecated and can introduce leaks; OWASP recommends "0".
                headers["X-XSS-Protection"] = "0"
                headers["X-Request-ID"] = request_id
                if path.startswith("/api/"):
                    headers["Cache-Control"] = "no-store"
                    headers["Pragma"] = "no-cache"
                if "server" in headers:
                    del headers["server"]
            await send(message)

        await self.app(scope, receive, send_wrapper)


class BodySizeLimitMiddleware:
    """OWASP API4: reject request bodies larger than ``max_bytes`` with HTTP 413.

    Checks the declared Content-Length and also counts streamed bytes so that
    chunked uploads cannot bypass the limit.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = self.max_bytes + 1
                if declared > self.max_bytes:
                    await self._reject(send)
                    return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _PayloadTooLarge()
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _PayloadTooLarge:
            if not response_started:
                await self._reject(send)

    @staticmethod
    async def _reject(send: Send) -> None:
        body = b'{"detail":"Request body too large."}'
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})


class _PayloadTooLarge(Exception):
    pass
