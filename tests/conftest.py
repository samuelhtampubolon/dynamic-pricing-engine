"""Shared pytest fixtures.

The suite runs against a throw-away DuckDB file so it never touches the
developer's local lakehouse, and works on a fresh clone with no seeded data.
"""

import os
import shutil
import tempfile
from pathlib import Path

_TMP_DIR = tempfile.mkdtemp(prefix="pricing_engine_tests_")
os.environ["DUCKDB_PATH"] = str(Path(_TMP_DIR) / "test_lakehouse.duckdb")
os.environ["DUCKDB_READ_ONLY"] = "false"
os.environ["APP_ENV"] = "test"
os.environ.pop("JWT_SECRET_KEY", None)
os.environ.pop("TENANT_CREDENTIALS", None)

import pytest  # noqa: E402

from src.lakehouse import lakehouse  # noqa: E402
from src.security import limiter  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def setup_test_lakehouse():
    """Seed the temporary lakehouse once for the whole session."""
    from src.seed_market_data import seed_synthetic_lakehouse
    seed_synthetic_lakehouse(days=180, seed=123)
    yield
    lakehouse.close()
    shutil.rmtree(_TMP_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Each test starts with fresh rate-limit buckets."""
    limiter.reset()
    yield
