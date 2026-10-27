"""DuckDB Medallion Lakehouse manager (Bronze, Silver, Gold) with Automated Quality Gates."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

import duckdb

from src.config import settings

# Thread-safety re-entrant lock
_db_lock = threading.RLock()

# Fixed allowlist: table names are never taken from user input.
MEDALLION_TABLES = (
    "bronze_market_events",
    "silver_validated_demand",
    "gold_sku_elasticity",
    "gold_cohort_economics",
    "sku_catalog",
)

# Columns added after v1. ALTER ... ADD COLUMN IF NOT EXISTS migrates older lakehouse files in place.
_MIGRATIONS = (
    ("gold_sku_elasticity", "cross_elasticity_beta", "DOUBLE"),
    ("gold_sku_elasticity", "alpha_intercept", "DOUBLE"),
    ("gold_sku_elasticity", "smearing_factor", "DOUBLE"),
    ("gold_sku_elasticity", "elasticity_std_error", "DOUBLE"),
    ("gold_sku_elasticity", "adj_r_squared", "DOUBLE"),
    ("gold_sku_elasticity", "sample_size", "INTEGER"),
    ("gold_sku_elasticity", "raw_optimal_price", "DOUBLE"),
    ("gold_cohort_economics", "new_customers", "INTEGER"),
    ("audit_quality_gates", "quarantine_ratio", "DOUBLE"),
)

# 1.4826 * MAD is a consistent estimator of sigma for Gaussian data.
_MAD_TO_SIGMA = 1.4826


class LakehouseReadOnlyError(PermissionError):
    """Raised when a write is attempted through a read-only serving connection."""


class LakehouseManager:
    """Manages DuckDB Medallion Lakehouse tables, data promotion, and query execution.

    Serving processes (FastAPI, Streamlit) run with ``DUCKDB_READ_ONLY=true`` so
    several processes can attach to the same file concurrently; only the
    seeding / preflight step opens the file read-write, before the servers start.
    """

    def __init__(self, db_path: Optional[Path] = None, read_only: Optional[bool] = None):
        self.db_path = str(db_path or settings.DUCKDB_PATH)
        self.read_only = settings.DUCKDB_READ_ONLY if read_only is None else read_only
        self._shared_conn: Optional[duckdb.DuckDBPyConnection] = None

    def get_raw_connection(self, read_only: bool = False) -> duckdb.DuckDBPyConnection:
        """Return the process-wide connection, refusing writes on a read-only lakehouse."""
        if not read_only and self.read_only:
            raise LakehouseReadOnlyError("Lakehouse is attached read-only in this process.")
        if self._shared_conn is None:
            self._shared_conn = duckdb.connect(database=self.db_path, read_only=self.read_only)
        return self._shared_conn

    @contextmanager
    def get_connection(self, read_only: bool = False) -> Generator[duckdb.DuckDBPyConnection, None, None]:
        """Context manager providing a thread-safe DuckDB connection."""
        with _db_lock:
            yield self.get_raw_connection(read_only=read_only)

    def close(self) -> None:
        """Close shared connection (releases the file lock)."""
        with _db_lock:
            if self._shared_conn is not None:
                try:
                    self._shared_conn.close()
                finally:
                    self._shared_conn = None

    def initialize_schema(self) -> None:
        """Create Bronze, Silver, Gold tables and SKU catalog, and apply migrations."""
        with self.get_connection(read_only=False) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sku_catalog (
                    sku_id VARCHAR NOT NULL,
                    tenant_id VARCHAR NOT NULL,
                    sku_name VARCHAR NOT NULL,
                    unit_cogs DOUBLE NOT NULL CHECK (unit_cogs > 0),
                    min_margin_pct DOUBLE NOT NULL CHECK (min_margin_pct >= 0),
                    max_msrp DOUBLE NOT NULL,
                    base_price DOUBLE NOT NULL,
                    PRIMARY KEY (sku_id, tenant_id),
                    CHECK (max_msrp >= unit_cogs * (1 + min_margin_pct)),
                    CHECK (base_price > unit_cogs AND base_price <= max_msrp)
                );
            """)

            # Bronze: Raw Ingested Market Events
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bronze_market_events (
                    event_id VARCHAR NOT NULL,
                    tenant_id VARCHAR NOT NULL,
                    sku_id VARCHAR NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    observed_price DOUBLE NOT NULL,
                    competitor_price DOUBLE NOT NULL,
                    units_sold INTEGER NOT NULL,
                    inventory_level INTEGER NOT NULL,
                    is_rejected_poison BOOLEAN DEFAULT FALSE,
                    rejection_reason VARCHAR
                );
            """)

            # Silver: Cleaned, Validated, and Aggregated Daily Demand
            conn.execute("""
                CREATE TABLE IF NOT EXISTS silver_validated_demand (
                    sku_id VARCHAR NOT NULL,
                    tenant_id VARCHAR NOT NULL,
                    date_bucket DATE NOT NULL,
                    avg_price DOUBLE NOT NULL,
                    avg_comp_price DOUBLE NOT NULL,
                    total_qty INTEGER NOT NULL,
                    unit_cogs DOUBLE NOT NULL,
                    min_margin_pct DOUBLE NOT NULL,
                    max_msrp DOUBLE NOT NULL,
                    PRIMARY KEY (sku_id, tenant_id, date_bucket)
                );
            """)

            # Gold: Econometric SKU Elasticity & Guarded Pricing Recommendations
            conn.execute("""
                CREATE TABLE IF NOT EXISTS gold_sku_elasticity (
                    sku_id VARCHAR NOT NULL,
                    tenant_id VARCHAR NOT NULL,
                    elasticity_epsilon DOUBLE NOT NULL,
                    r_squared DOUBLE NOT NULL,
                    recommended_price DOUBLE NOT NULL,
                    expected_margin_uplift_pct DOUBLE NOT NULL,
                    updated_at TIMESTAMP NOT NULL,
                    PRIMARY KEY (sku_id, tenant_id)
                );
            """)

            # Gold: Cohort Unit Economics (LTV, CAC, Payback)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS gold_cohort_economics (
                    cohort_id VARCHAR NOT NULL,
                    tenant_id VARCHAR NOT NULL,
                    arpu_usd DOUBLE NOT NULL,
                    gross_margin_pct DOUBLE NOT NULL,
                    churn_rate DOUBLE NOT NULL,
                    cac_usd DOUBLE NOT NULL,
                    ltv_usd DOUBLE NOT NULL,
                    ltv_cac_ratio DOUBLE NOT NULL,
                    PRIMARY KEY (cohort_id, tenant_id)
                );
            """)

            # Data Quality Audit Logs
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_quality_gates (
                    gate_id VARCHAR PRIMARY KEY,
                    run_timestamp TIMESTAMP NOT NULL,
                    tenant_id VARCHAR NOT NULL,
                    total_bronze_records INTEGER NOT NULL,
                    passed_silver_records INTEGER NOT NULL,
                    rejected_poison_records INTEGER NOT NULL,
                    status VARCHAR NOT NULL
                );
            """)

            for table, column, col_type in _MIGRATIONS:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_type}")

    def is_current_schema_populated(self) -> bool:
        """True if Bronze has data and every Gold elasticity row carries v2 model parameters."""
        try:
            with self.get_connection(read_only=True) as conn:
                bronze = conn.execute("SELECT COUNT(*) FROM bronze_market_events").fetchone()[0]
                gold_total, gold_v2 = conn.execute(
                    "SELECT COUNT(*), COUNT(alpha_intercept) FROM gold_sku_elasticity"
                ).fetchone()
                cohorts = conn.execute(
                    "SELECT COUNT(*), COUNT(new_customers) FROM gold_cohort_economics"
                ).fetchone()
        except duckdb.Error:
            return False
        return bronze > 0 and gold_total > 0 and gold_total == gold_v2 and cohorts[0] > 0 and cohorts[0] == cohorts[1]

    def run_anti_poisoning_quality_gate(self, tenant_id: str) -> Dict[str, Any]:
        """OWASP Anti-Poisoning Gate (Bronze -> Silver).

        Quarantines events whose price, competitor price or volume has a robust
        z-score above the tolerance (default 5.0):

            z_i = (x_i - median(x)) / (1.4826 * MAD(x))

        The median/MAD estimator has a 50% breakdown point, so a coordinated batch
        of poisoned events cannot inflate the dispersion and hide itself (the
        "masking" weakness of the mean/std z-score). Non-positive values are
        always quarantined. If the quarantined share exceeds MAX_QUARANTINE_RATIO
        the gate fails closed and the previous Silver snapshot is kept.
        """
        tol = settings.MAX_Z_SCORE_TOLERANCE
        with self.get_connection(read_only=False) as conn:
            # Idempotent re-run: start from a clean slate for this tenant.
            conn.execute("""
                UPDATE bronze_market_events
                SET is_rejected_poison = FALSE, rejection_reason = NULL
                WHERE tenant_id = ?
            """, [tenant_id])

            conn.execute(f"""
                UPDATE bronze_market_events AS b
                SET is_rejected_poison = TRUE,
                    rejection_reason = CASE
                        WHEN b.observed_price <= 0 OR b.units_sold <= 0 OR b.competitor_price <= 0
                             OR b.inventory_level < 0 THEN 'NON_POSITIVE_VALUE'
                        WHEN ABS(b.observed_price - s.med_p) / s.sig_p > ? THEN 'PRICE_ROBUST_Z_GT_TOL'
                        WHEN ABS(b.units_sold - s.med_q) / s.sig_q > ? THEN 'VOLUME_ROBUST_Z_GT_TOL'
                        ELSE 'COMPETITOR_PRICE_ROBUST_Z_GT_TOL'
                    END
                FROM (
                    SELECT
                        sku_id,
                        MEDIAN(observed_price) AS med_p,
                        COALESCE(NULLIF({_MAD_TO_SIGMA} * MAD(observed_price), 0),
                                 NULLIF(STDDEV_POP(observed_price), 0), 1.0) AS sig_p,
                        MEDIAN(units_sold) AS med_q,
                        COALESCE(NULLIF({_MAD_TO_SIGMA} * MAD(units_sold), 0),
                                 NULLIF(STDDEV_POP(units_sold), 0), 1.0) AS sig_q,
                        MEDIAN(competitor_price) AS med_c,
                        COALESCE(NULLIF({_MAD_TO_SIGMA} * MAD(competitor_price), 0),
                                 NULLIF(STDDEV_POP(competitor_price), 0), 1.0) AS sig_c
                    FROM bronze_market_events
                    WHERE tenant_id = ?
                    GROUP BY sku_id
                ) AS s
                WHERE b.tenant_id = ?
                  AND b.sku_id = s.sku_id
                  AND (
                      b.observed_price <= 0 OR b.units_sold <= 0 OR b.competitor_price <= 0
                      OR b.inventory_level < 0
                      OR ABS(b.observed_price - s.med_p) / s.sig_p > ?
                      OR ABS(b.units_sold - s.med_q) / s.sig_q > ?
                      OR ABS(b.competitor_price - s.med_c) / s.sig_c > ?
                  )
            """, [tol, tol, tenant_id, tenant_id, tol, tol, tol])

            total_bronze = conn.execute(
                "SELECT COUNT(*) FROM bronze_market_events WHERE tenant_id = ?", [tenant_id]
            ).fetchone()[0]
            actual_rejected = conn.execute(
                "SELECT COUNT(*) FROM bronze_market_events WHERE tenant_id = ? AND is_rejected_poison = TRUE",
                [tenant_id],
            ).fetchone()[0]
            ratio = (actual_rejected / total_bronze) if total_bronze else 0.0
            gate_status = "PASSED" if ratio <= settings.MAX_QUARANTINE_RATIO else "FAILED_QUARANTINE_RATIO"

            if gate_status == "PASSED":
                conn.execute("DELETE FROM silver_validated_demand WHERE tenant_id = ?", [tenant_id])
                conn.execute("""
                    INSERT INTO silver_validated_demand
                    SELECT
                        b.sku_id,
                        b.tenant_id,
                        CAST(b.timestamp AS DATE) AS date_bucket,
                        AVG(b.observed_price) AS avg_price,
                        AVG(b.competitor_price) AS avg_comp_price,
                        CAST(SUM(b.units_sold) AS INTEGER) AS total_qty,
                        c.unit_cogs,
                        c.min_margin_pct,
                        c.max_msrp
                    FROM bronze_market_events b
                    JOIN sku_catalog c
                      ON b.sku_id = c.sku_id AND b.tenant_id = c.tenant_id
                    WHERE b.tenant_id = ?
                      AND (b.is_rejected_poison IS FALSE OR b.is_rejected_poison IS NULL)
                    GROUP BY
                        b.sku_id, b.tenant_id, CAST(b.timestamp AS DATE),
                        c.unit_cogs, c.min_margin_pct, c.max_msrp;
                """, [tenant_id])

            passed_silver = conn.execute(
                "SELECT COUNT(*) FROM silver_validated_demand WHERE tenant_id = ?", [tenant_id]
            ).fetchone()[0]

            reasons = dict(conn.execute("""
                SELECT rejection_reason, COUNT(*) FROM bronze_market_events
                WHERE tenant_id = ? AND is_rejected_poison = TRUE
                GROUP BY rejection_reason
            """, [tenant_id]).fetchall())

            gate_id = f"gate_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{tenant_id}"
            conn.execute("""
                INSERT OR REPLACE INTO audit_quality_gates (
                    gate_id, run_timestamp, tenant_id, total_bronze_records, passed_silver_records,
                    rejected_poison_records, status, quarantine_ratio
                ) VALUES (?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?)
            """, [gate_id, tenant_id, total_bronze, passed_silver, actual_rejected, gate_status, ratio])

            return {
                "gate_id": gate_id,
                "tenant_id": tenant_id,
                "total_bronze": total_bronze,
                "passed_silver_buckets": passed_silver,
                "rejected_poison_count": actual_rejected,
                "quarantine_ratio": round(ratio, 6),
                "rejection_reasons": reasons,
                "status": gate_status,
            }

    def get_table_counts(self) -> Dict[str, int]:
        """Return row counts for Medallion Lakehouse tables."""
        counts = {}
        with self.get_connection(read_only=True) as conn:
            for table in MEDALLION_TABLES:
                try:
                    res = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                    counts[table] = res[0] if res else 0
                except duckdb.Error:
                    counts[table] = 0
        return counts

    def get_sku_catalog(self, tenant_id: str) -> List[Dict[str, Any]]:
        """Fetch tenant-isolated SKU catalog."""
        with self.get_connection(read_only=True) as conn:
            rows = conn.execute("""
                SELECT sku_id, tenant_id, sku_name, unit_cogs, min_margin_pct, max_msrp, base_price
                FROM sku_catalog
                WHERE tenant_id = ?
                ORDER BY sku_id
            """, [tenant_id]).fetchall()
            return [
                {
                    "sku_id": r[0],
                    "tenant_id": r[1],
                    "sku_name": r[2],
                    "unit_cogs": r[3],
                    "min_margin_pct": r[4],
                    "max_msrp": r[5],
                    "base_price": r[6],
                }
                for r in rows
            ]


lakehouse = LakehouseManager()
