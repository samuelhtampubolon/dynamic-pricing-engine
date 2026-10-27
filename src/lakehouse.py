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


class LakehouseManager:
    """Manages DuckDB Medallion Lakehouse tables, data promotion, and query execution."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = str(db_path or settings.DUCKDB_PATH)
        self._shared_conn: Optional[duckdb.DuckDBPyConnection] = None

    def get_raw_connection(self, read_only: bool = False) -> duckdb.DuckDBPyConnection:
        """Return or create a managed connection.

        Reuses the open connection in the current process to prevent configuration collision.
        """
        if self._shared_conn is None:
            self._shared_conn = duckdb.connect(database=self.db_path, read_only=False)
        return self._shared_conn

    @contextmanager
    def get_connection(self, read_only: bool = False) -> Generator[duckdb.DuckDBPyConnection, None, None]:
        """Context manager providing thread-safe DuckDB connection."""
        with _db_lock:
            conn = self.get_raw_connection(read_only=read_only)
            try:
                yield conn
            finally:
                pass

    def close(self) -> None:
        """Close shared connection."""
        with _db_lock:
            if self._shared_conn is not None:
                try:
                    self._shared_conn.close()
                except Exception:
                    pass
                self._shared_conn = None

    def initialize_schema(self) -> None:
        """Create Bronze, Silver, Gold tables and SKU catalog if they do not exist."""
        with self.get_connection(read_only=False) as conn:
            # SKU master catalog
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sku_catalog (
                    sku_id VARCHAR NOT NULL,
                    tenant_id VARCHAR NOT NULL,
                    sku_name VARCHAR NOT NULL,
                    unit_cogs DOUBLE NOT NULL,
                    min_margin_pct DOUBLE NOT NULL,
                    max_msrp DOUBLE NOT NULL,
                    base_price DOUBLE NOT NULL,
                    PRIMARY KEY (sku_id, tenant_id)
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

    def run_anti_poisoning_quality_gate(self, tenant_id: str) -> Dict[str, Any]:
        """OWASP Anti-Poisoning Gate:

        Detect and quarantine market event anomalies where price or units_sold
        deviate by more than 5 standard deviations (z-score > 5.0) or are non-positive.
        Promotes clean events from Bronze into Silver daily aggregations.
        """
        with self.get_connection(read_only=False) as conn:
            sku_stats = conn.execute("""
                SELECT 
                    sku_id,
                    AVG(observed_price) as avg_p,
                    STDDEV_POP(observed_price) as std_p,
                    AVG(units_sold) as avg_q,
                    STDDEV_POP(units_sold) as std_q
                FROM bronze_market_events
                WHERE tenant_id = ?
                GROUP BY sku_id
            """, [tenant_id]).fetchall()

            rejected_count = 0
            for sku_id, avg_p, std_p, avg_q, std_q in sku_stats:
                std_p_val = std_p if std_p and std_p > 0 else 1.0
                std_q_val = std_q if std_q and std_q > 0 else 1.0

                conn.execute("""
                    UPDATE bronze_market_events
                    SET is_rejected_poison = TRUE,
                        rejection_reason = 'Z-Score > 5.0 or non-positive value'
                    WHERE tenant_id = ? 
                      AND sku_id = ?
                      AND (
                          observed_price <= 0 
                          OR units_sold <= 0
                          OR ABS((observed_price - ?) / ?) > ?
                          OR ABS((units_sold - ?) / ?) > ?
                      )
                """, [
                    tenant_id,
                    sku_id,
                    avg_p,
                    std_p_val,
                    settings.MAX_Z_SCORE_TOLERANCE,
                    avg_q,
                    std_q_val,
                    settings.MAX_Z_SCORE_TOLERANCE,
                ])
                rejected_count += 1

            conn.execute("DELETE FROM silver_validated_demand WHERE tenant_id = ?", [tenant_id])

            conn.execute("""
                INSERT INTO silver_validated_demand
                SELECT 
                    b.sku_id,
                    b.tenant_id,
                    CAST(b.timestamp AS DATE) as date_bucket,
                    AVG(b.observed_price) as avg_price,
                    AVG(b.competitor_price) as avg_comp_price,
                    CAST(SUM(b.units_sold) AS INTEGER) as total_qty,
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

            total_bronze = conn.execute(
                "SELECT COUNT(*) FROM bronze_market_events WHERE tenant_id = ?", [tenant_id]
            ).fetchone()[0]
            passed_silver = conn.execute(
                "SELECT COUNT(*) FROM silver_validated_demand WHERE tenant_id = ?", [tenant_id]
            ).fetchone()[0]
            actual_rejected = conn.execute(
                "SELECT COUNT(*) FROM bronze_market_events WHERE tenant_id = ? AND is_rejected_poison = TRUE",
                [tenant_id]
            ).fetchone()[0]

            gate_id = f"gate_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{tenant_id}"
            conn.execute("""
                INSERT OR REPLACE INTO audit_quality_gates VALUES (?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?)
            """, [gate_id, tenant_id, total_bronze, passed_silver, actual_rejected, "PASSED"])

            return {
                "gate_id": gate_id,
                "tenant_id": tenant_id,
                "total_bronze": total_bronze,
                "passed_silver_buckets": passed_silver,
                "rejected_poison_count": actual_rejected,
                "status": "PASSED",
            }

    def get_table_counts(self) -> Dict[str, int]:
        """Return row counts for Medallion Lakehouse tables."""
        counts = {}
        with self.get_connection(read_only=False) as conn:
            for table in [
                "bronze_market_events",
                "silver_validated_demand",
                "gold_sku_elasticity",
                "gold_cohort_economics",
                "sku_catalog",
            ]:
                try:
                    res = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                    counts[table] = res[0] if res else 0
                except Exception:
                    counts[table] = 0
        return counts

    def get_sku_catalog(self, tenant_id: str) -> List[Dict[str, Any]]:
        """Fetch tenant-isolated SKU catalog."""
        with self.get_connection(read_only=False) as conn:
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
