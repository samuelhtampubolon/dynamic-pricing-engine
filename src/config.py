"""Central configuration module for Dynamic Pricing & Unit Economics Engine."""

from __future__ import annotations
import os
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


class Settings(BaseModel):
    """Application settings and environment configurations."""

    PROJECT_NAME: str = "Real-Time Dynamic Pricing & Unit Economics Engine"
    VERSION: str = "1.0.0"
    ENV: str = Field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    DEBUG: bool = Field(default_factory=lambda: os.getenv("APP_DEBUG", "True").lower() == "true")

    # Network binding
    HOST: str = Field(default_factory=lambda: os.getenv("HOST", "127.0.0.1"))
    API_PORT: int = Field(default_factory=lambda: int(os.getenv("API_PORT", "8000")))
    STREAMLIT_PORT: int = Field(default_factory=lambda: int(os.getenv("STREAMLIT_PORT", "8501")))

    # Security & JWT
    JWT_SECRET_KEY: str = Field(
        default_factory=lambda: os.getenv(
            "JWT_SECRET_KEY", "b40d7c79e6587522ff8217bb6937402dc4e9a80e46fc6b5839211dcae3ce0b61"
        )
    )
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 120

    # Rate Limiting
    RATE_LIMIT_DEFAULT: str = "100/minute"
    MAX_SIMULATION_HORIZON: int = 5000

    # DuckDB Lakehouse path
    DUCKDB_PATH: Path = Field(default_factory=lambda: Path(os.getenv("DUCKDB_PATH", str(DATA_DIR / "pricing_lakehouse.duckdb"))))

    # Guardrails
    DEFAULT_MIN_MARGIN_PCT: float = 0.20  # 20% minimum gross margin floor
    MAX_Z_SCORE_TOLERANCE: float = 5.0    # OWASP Anti-Poisoning Gate


settings = Settings()
