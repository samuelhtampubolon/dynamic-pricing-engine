"""Central configuration module for Dynamic Pricing & Unit Economics Engine.

Settings are read from environment variables and an optional ``.env`` file.
Security-sensitive values (JWT signing key, tenant credentials) have NO
hard-coded production defaults: in ``production`` the process refuses to start
without them, and in ``development``/``test`` an ephemeral per-process signing
key is generated so that no shared secret ever lives in source control.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("pricing_engine.config")

# SHA-256 digests of signing keys that were published in an earlier release.
# They are permanently compromised and are rejected even if configured. Only the
# digests are kept, so the keys themselves never appear in source control.
_COMPROMISED_JWT_SECRET_SHA256 = frozenset({
    "c85c055b87419a5bbc2d45099b13f0e8259b92db06056bcc7a71a5823aa0420d",
})
_MIN_JWT_SECRET_LENGTH = 32


class Settings(BaseSettings):
    """Application settings and environment configurations."""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    PROJECT_NAME: str = "Real-Time Dynamic Pricing & Unit Economics Engine"
    VERSION: str = "2.0.0"
    ENV: Literal["development", "test", "production"] = Field("development", validation_alias="APP_ENV")
    DEBUG: bool = Field(False, validation_alias="APP_DEBUG")

    # Network binding (loopback by default)
    HOST: str = "127.0.0.1"
    API_PORT: int = Field(8000, ge=1, le=65535)
    STREAMLIT_PORT: int = Field(8501, ge=1, le=65535)
    ALLOWED_HOSTS: str = "127.0.0.1,localhost,testserver"
    CORS_ALLOW_ORIGINS: str = "http://127.0.0.1:8501,http://localhost:8501"

    # Security & JWT
    JWT_SECRET_KEY: Optional[SecretStr] = None
    JWT_ALGORITHM: Literal["HS256", "HS384", "HS512"] = "HS256"
    JWT_ISSUER: str = "dynamic-pricing-engine"
    JWT_AUDIENCE: str = "pricing-api"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(30, ge=1, le=240)
    # Comma separated "tenant_id:role:scrypt$<salt>$<hash>" entries.
    # Generate a hash with: python -m src.credentials hash-secret
    TENANT_CREDENTIALS: Optional[SecretStr] = None

    # Rate limiting & resource consumption (OWASP API4)
    RATE_LIMIT_DEFAULT: str = "100/minute"
    RATE_LIMIT_AUTH: str = "10/minute"
    MAX_REQUEST_BYTES: int = Field(32_768, ge=1_024, le=1_048_576)
    MAX_SIMULATION_HORIZON: int = 5000

    # DuckDB Lakehouse path. Serving processes open the file read-only so the
    # API and the Streamlit cockpit can share it without file-lock collisions.
    DUCKDB_PATH: Path = DATA_DIR / "pricing_lakehouse.duckdb"
    DUCKDB_READ_ONLY: bool = False

    # Guardrails & data quality
    DEFAULT_MIN_MARGIN_PCT: float = 0.20      # markup on COGS used for the margin floor
    MAX_Z_SCORE_TOLERANCE: float = 5.0        # OWASP Anti-Poisoning Gate (robust z)
    MAX_QUARANTINE_RATIO: float = 0.20        # gate fails closed above this reject share

    # Pricing policy parameters
    COMPETITOR_ANCHOR_WEIGHT: float = Field(0.30, ge=0.0, le=1.0)
    SELL_THROUGH_MIN_DAYS: float = Field(7.0, gt=0.0)
    SELL_THROUGH_MAX_DAYS: float = Field(45.0, gt=0.0)
    PRIOR_ELASTICITY: float = Field(-2.0, lt=-1.0)

    # Unit economics
    ANNUAL_DISCOUNT_RATE: float = Field(0.10, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_security_posture(self) -> "Settings":
        if self.SELL_THROUGH_MIN_DAYS >= self.SELL_THROUGH_MAX_DAYS:
            raise ValueError("SELL_THROUGH_MIN_DAYS must be lower than SELL_THROUGH_MAX_DAYS.")

        secret = self.JWT_SECRET_KEY.get_secret_value() if self.JWT_SECRET_KEY else ""
        if secret and hashlib.sha256(secret.encode("utf-8")).hexdigest() in _COMPROMISED_JWT_SECRET_SHA256:
            raise ValueError("JWT_SECRET_KEY is a publicly known value. Generate a new one.")

        if self.ENV == "production":
            if len(secret) < _MIN_JWT_SECRET_LENGTH:
                raise ValueError(
                    f"JWT_SECRET_KEY must be set to at least {_MIN_JWT_SECRET_LENGTH} characters in production."
                )
            if not self.TENANT_CREDENTIALS or not self.TENANT_CREDENTIALS.get_secret_value().strip():
                raise ValueError("TENANT_CREDENTIALS must be configured in production.")
            if self.DEBUG:
                raise ValueError("APP_DEBUG must be false in production.")
        elif len(secret) < _MIN_JWT_SECRET_LENGTH:
            # Ephemeral per-process key: tokens die with the process, nothing to leak.
            if secret:
                logger.warning("JWT_SECRET_KEY shorter than %d chars; using an ephemeral key.", _MIN_JWT_SECRET_LENGTH)
            self.JWT_SECRET_KEY = SecretStr(secrets.token_urlsafe(48))
        return self

    @property
    def allowed_hosts(self) -> list[str]:
        return [h.strip() for h in self.ALLOWED_HOSTS.split(",") if h.strip()]

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ALLOW_ORIGINS.split(",") if o.strip() and o.strip() != "*"]

    @property
    def jwt_secret(self) -> str:
        assert self.JWT_SECRET_KEY is not None
        return self.JWT_SECRET_KEY.get_secret_value()


settings = Settings()
