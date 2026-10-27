"""Seed script generating 12 months of synthetic multi-SKU market events and cohort economics."""

from __future__ import annotations
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict
import numpy as np

from src.lakehouse import lakehouse
from src.services.elasticity_model import elasticity_service
from src.services.unit_economics import unit_economics_service

# Realistic SKU Catalog across 2 sample tenants
CATALOG_DEFINITIONS = [
    # Tenant Alpha: Enterprise Computing & High-Tech Hardware
    {
        "sku_id": "SKU-GPU-H100",
        "tenant_id": "tenant_alpha",
        "sku_name": "Tensor-Core GPU Compute Blade 80GB",
        "unit_cogs": 21500.0,
        "min_margin_pct": 0.22,  # 22% margin floor -> $26,230
        "max_msrp": 39500.0,
        "base_price": 31000.0,
        "true_elasticity": -2.60,
        "base_daily_demand": 24,
    },
    {
        "sku_id": "SKU-NET-SWITCH-400G",
        "tenant_id": "tenant_alpha",
        "sku_name": "Ultra-Low Latency 400GbE Spine Switch",
        "unit_cogs": 4800.0,
        "min_margin_pct": 0.25,  # 25% margin floor -> $6,000
        "max_msrp": 9200.0,
        "base_price": 7500.0,
        "true_elasticity": -2.40,
        "base_daily_demand": 50,
    },
    {
        "sku_id": "SKU-NVME-STORAGE-30TB",
        "tenant_id": "tenant_alpha",
        "sku_name": "Gen5 Enterprise NVMe U.3 30.72TB SSD",
        "unit_cogs": 1850.0,
        "min_margin_pct": 0.20,  # 20% margin floor -> $2,220
        "max_msrp": 3800.0,
        "base_price": 2900.0,
        "true_elasticity": -2.90,
        "base_daily_demand": 120,
    },
    # Tenant Beta: Industrial Automation & Precision Engineering
    {
        "sku_id": "SKU-CNC-SPINDLE",
        "tenant_id": "tenant_beta",
        "sku_name": "5-Axis High-Speed CNC Electro-Spindle",
        "unit_cogs": 3400.0,
        "min_margin_pct": 0.25,  # 25% margin floor -> $4,250
        "max_msrp": 6900.0,
        "base_price": 5400.0,
        "true_elasticity": -2.50,
        "base_daily_demand": 35,
    },
    {
        "sku_id": "SKU-COLD-REEFER",
        "tenant_id": "tenant_beta",
        "sku_name": "IoT Cold-Chain Telematics Controller",
        "unit_cogs": 420.0,
        "min_margin_pct": 0.30,  # 30% margin floor -> $546
        "max_msrp": 1150.0,
        "base_price": 820.0,
        "true_elasticity": -3.20,
        "base_daily_demand": 210,
    },
]


def _validate_catalog_item(item: Dict[str, Any]) -> None:
    floor = item["unit_cogs"] * (1.0 + item["min_margin_pct"])
    if not (0 < item["unit_cogs"] < item["base_price"] <= item["max_msrp"] and floor <= item["max_msrp"]):
        raise ValueError(f"Inconsistent guardrails for {item['sku_id']}.")


def seed_synthetic_lakehouse(days: int = 365, seed: int = 42) -> Dict[str, Any]:
    """Populate 12 months of synthetic market data into DuckDB Medallion Lakehouse."""
    rng = np.random.default_rng(seed)

    lakehouse.initialize_schema()

    with lakehouse.get_connection(read_only=False) as conn:
        # 1. Seed SKU Catalog
        for item in CATALOG_DEFINITIONS:
            _validate_catalog_item(item)
            conn.execute("""
                INSERT OR REPLACE INTO sku_catalog (
                    sku_id, tenant_id, sku_name, unit_cogs, min_margin_pct, max_msrp, base_price
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, [
                item["sku_id"],
                item["tenant_id"],
                item["sku_name"],
                item["unit_cogs"],
                item["min_margin_pct"],
                item["max_msrp"],
                item["base_price"],
            ])

        # Clear existing Bronze / Silver / Gold state to start fresh
        for table in ("bronze_market_events", "silver_validated_demand", "gold_sku_elasticity"):
            conn.execute(f"DELETE FROM {table}")

        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=days)

        bronze_records = []
        event_counter = 1

        for item in CATALOG_DEFINITIONS:
            sku_id = item["sku_id"]
            tenant_id = item["tenant_id"]
            base_p = item["base_price"]
            elasticity = item["true_elasticity"]
            base_q = item["base_daily_demand"]

            current_dt = start_date
            while current_dt <= end_date:
                dow_factor = 1.0 + 0.15 * math.sin(current_dt.weekday() * math.pi / 3.0)
                competitor_p = base_p * rng.uniform(0.92, 1.08)
                observed_p = base_p * rng.uniform(0.82, 1.18)

                price_ratio = observed_p / base_p
                comp_ratio = competitor_p / base_p
                exp_q = base_q * (price_ratio ** elasticity) * (comp_ratio ** 0.35) * dow_factor

                actual_q = max(1, int(round(rng.normal(exp_q, max(2.0, exp_q * 0.15)))))
                inv_level = int(base_q * rng.uniform(10, 40))

                event_id = f"evt_{event_counter:07d}"
                bronze_records.append((
                    event_id,
                    tenant_id,
                    sku_id,
                    current_dt,
                    round(float(observed_p), 2),
                    round(float(competitor_p), 2),
                    int(actual_q),
                    int(inv_level),
                    False,
                    None,
                ))
                event_counter += 1
                current_dt += timedelta(days=1)

            # Intentionally inject 2 extreme poisoned outlier events for this SKU
            # to verify OWASP anti-poisoning z-score quality gate
            poison_event_1 = (
                f"evt_{event_counter:07d}_poison",
                tenant_id,
                sku_id,
                end_date - timedelta(days=5),
                round(base_p * 8.5, 2),  # +750% price spike (extreme z-score > 7)
                round(base_p, 2),
                1,
                50,
                False,
                None,
            )
            event_counter += 1
            poison_event_2 = (
                f"evt_{event_counter:07d}_poison",
                tenant_id,
                sku_id,
                end_date - timedelta(days=3),
                round(base_p * 0.9, 2),
                round(base_p, 2),
                int(base_q * 25),  # 25x volume injection (extreme z-score > 8)
                20,
                False,
                None,
            )
            event_counter += 1
            bronze_records.append(poison_event_1)
            bronze_records.append(poison_event_2)

        # Batch insert into Bronze
        conn.executemany("""
            INSERT INTO bronze_market_events (
                event_id, tenant_id, sku_id, timestamp, observed_price, competitor_price,
                units_sold, inventory_level, is_rejected_poison, rejection_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, bronze_records)

    # 2. Run Anti-Poisoning Quality Gate for each tenant (Bronze -> Silver)
    gate_results = {}
    for t_id in ["tenant_alpha", "tenant_beta"]:
        gate_results[t_id] = lakehouse.run_anti_poisoning_quality_gate(t_id)

    # 3. Fit & Persist Gold SKU Elasticity
    for t_id in ["tenant_alpha", "tenant_beta"]:
        elasticity_service.train_and_persist_gold_elasticity(t_id)

    # 4. Populate Gold Cohort Economics (12 monthly cohorts per tenant)
    with lakehouse.get_connection(read_only=False) as conn:
        conn.execute("DELETE FROM gold_cohort_economics")

        cohort_months = [
            f"2025-{m:02d}" for m in range(1, 13)
        ]

        cohort_records = []
        for t_id in ["tenant_alpha", "tenant_beta"]:
            base_arpu = 3800.0 if t_id == "tenant_alpha" else 1450.0
            base_cac = 4200.0 if t_id == "tenant_alpha" else 1850.0
            base_customers = 140 if t_id == "tenant_alpha" else 380

            for idx, c_month in enumerate(cohort_months):
                efficiency = 1.0 + (idx * 0.02)
                arpu = round(base_arpu * rng.uniform(0.95, 1.08), 2)
                gm_pct = round(rng.uniform(0.48, 0.58), 4)
                churn = round(rng.uniform(0.025, 0.045) / efficiency, 4)
                cac = round((base_cac / efficiency) * rng.uniform(0.94, 1.05), 2)

                ltv = unit_economics_service.calculate_ltv(arpu, gm_pct, churn)
                ltv_cac = unit_economics_service.calculate_ltv_cac_ratio(ltv, cac)
                new_customers = int(base_customers * efficiency * rng.uniform(0.85, 1.15))

                cohort_records.append((
                    c_month,
                    t_id,
                    arpu,
                    gm_pct,
                    churn,
                    cac,
                    ltv,
                    ltv_cac,
                    new_customers,
                ))

        conn.executemany("""
            INSERT INTO gold_cohort_economics (
                cohort_id, tenant_id, arpu_usd, gross_margin_pct, churn_rate,
                cac_usd, ltv_usd, ltv_cac_ratio, new_customers
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, cohort_records)

    counts = lakehouse.get_table_counts()
    return {
        "status": "Lakehouse successfully seeded",
        "table_counts": counts,
        "quality_gates": gate_results,
    }


if __name__ == "__main__":
    result = seed_synthetic_lakehouse()
    print("Seed Completed Successfully:")
    print(result)
