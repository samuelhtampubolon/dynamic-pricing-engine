"""Comprehensive pytest suite for Pricing Guardrails, Circuit Breakers, and Econometrics."""

import pytest
from src.lakehouse import lakehouse
from src.services.bandit_simulator import bandit_service
from src.services.elasticity_model import elasticity_service
from src.services.unit_economics import unit_economics_service


def test_margin_floor_circuit_breaker_cannot_be_violated():
    """CRITICAL RULE: No input combination can ever produce a price below unit_cogs * (1 + min_margin)."""
    tenant_id = "tenant_alpha"
    sku_id = "SKU-GPU-H100"

    # Extreme scenario: competitor dumps price at $1, inventory is flooded at 50,000 units
    quote = elasticity_service.compute_guarded_quote(
        tenant_id=tenant_id,
        sku_id=sku_id,
        inventory_level=50000,
        competitor_price=10.0,
        demand_surge_factor=0.1,
    )

    floor = quote["margin_floor_price"]
    rec = quote["recommended_price"]

    assert rec >= floor, f"Recommended price {rec} violated margin floor {floor}!"
    assert quote["guardrail_status"] == "CLAMPED_TO_MARGIN_FLOOR"
    markup_on_cogs = (rec - quote["unit_cogs"]) / quote["unit_cogs"]
    assert markup_on_cogs >= quote["min_margin_pct"] - 0.001


def test_msrp_ceiling_guardrail_cannot_be_exceeded():
    """CRITICAL RULE: Extreme demand surges cannot price gouge beyond max_msrp."""
    tenant_id = "tenant_alpha"
    sku_id = "SKU-GPU-H100"

    # Extreme scenario: inventory is near zero, demand surge factor 10.0x
    quote = elasticity_service.compute_guarded_quote(
        tenant_id=tenant_id,
        sku_id=sku_id,
        inventory_level=1,
        competitor_price=99999.0,
        demand_surge_factor=10.0,
    )

    msrp = quote["max_msrp_ceiling"]
    rec = quote["recommended_price"]

    assert rec <= msrp, f"Recommended price {rec} exceeded MSRP ceiling {msrp}!"
    assert quote["guardrail_status"] == "CLAMPED_TO_MSRP_CEILING"


def test_anti_poisoning_quality_gate_quarantines_anomalies():
    """OWASP Anti-Poisoning Gate: Market events with z-score > 5.0 are quarantined."""
    tenant_id = "tenant_alpha"
    gate_res = lakehouse.run_anti_poisoning_quality_gate(tenant_id)

    assert gate_res["status"] == "PASSED"
    assert gate_res["rejected_poison_count"] > 0, "Poisoned events were not quarantined!"

    # Verify no poisoned events exist in Silver
    with lakehouse.get_connection(read_only=True) as conn:
        poison_in_silver = conn.execute("""
            SELECT COUNT(*) 
            FROM silver_validated_demand
            WHERE tenant_id = ? AND (avg_price > 100000 OR total_qty > 5000)
        """, [tenant_id]).fetchone()[0]

        assert poison_in_silver == 0, "Poisoned market events leaked into Silver table!"


def test_unit_economics_governing_equations():
    """Verifies mathematical correctness of LTV, LTV/CAC, and Payback periods."""
    arpu = 2000.0
    gm_pct = 0.50
    churn = 0.05
    cac = 4000.0

    # LTV = (ARPU * GM%) / churn = (2000 * 0.5) / 0.05 = 20,000
    ltv = unit_economics_service.calculate_ltv(arpu, gm_pct, churn)
    assert ltv == 20000.0

    # LTV/CAC = 20,000 / 4,000 = 5.0
    ratio = unit_economics_service.calculate_ltv_cac_ratio(ltv, cac)
    assert ratio == 5.0

    # Payback months = CAC / (ARPU * GM%) = 4,000 / (2,000 * 0.5) = 4.0
    payback = unit_economics_service.calculate_payback_months(cac, arpu, gm_pct)
    assert payback == 4.0


def test_bandit_simulation_thompson_sampling():
    """Simulates Thompson Sampling multi-armed bandit pricing exploration."""
    tenant_id = "tenant_alpha"
    sku_id = "SKU-NET-SWITCH-400G"

    res = bandit_service.run_simulation(
        tenant_id=tenant_id,
        sku_id=sku_id,
        horizon=150,
        algorithm="thompson_sampling",
    )

    assert res["horizon"] == 150
    assert res["bandit_profit"] > 0
    assert res["cumulative_regret"] >= 0.0
    assert len(res["arm_allocations"]) >= 2
    assert res["step_history"] is not None and len(res["step_history"]) > 0


def test_bandit_simulation_epsilon_greedy():
    """Simulates Epsilon-Greedy multi-armed bandit pricing."""
    tenant_id = "tenant_beta"
    sku_id = "SKU-CNC-SPINDLE"

    res = bandit_service.run_simulation(
        tenant_id=tenant_id,
        sku_id=sku_id,
        horizon=100,
        algorithm="epsilon_greedy",
        epsilon_param=0.15,
    )

    assert res["algorithm"] == "epsilon_greedy"
    assert res["bandit_revenue"] > 0
    assert res["cumulative_regret"] >= 0.0
