"""Mathematical verification of the pricing, econometric, bandit and unit-economics formulas."""

import math
import random
import statistics
import time

import numpy as np
import pytest

from src.lakehouse import lakehouse
from src.services import pricing_math as pm
from src.services.bandit_simulator import bandit_service, run_bandit
from src.services.elasticity_model import elasticity_service, fit_log_log_ols
from src.services.unit_economics import unit_economics_service as ue

ALL_SKUS = [
    ("tenant_alpha", "SKU-GPU-H100"),
    ("tenant_alpha", "SKU-NET-SWITCH-400G"),
    ("tenant_alpha", "SKU-NVME-STORAGE-30TB"),
    ("tenant_beta", "SKU-CNC-SPINDLE"),
    ("tenant_beta", "SKU-COLD-REEFER"),
]


# ---------------------------------------------------------------------------
# Lerner optimum & margin algebra
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("eps", [-1.2, -1.85, -2.6, -4.0, -8.0])
def test_lerner_price_maximises_profit(eps):
    """P* = c eps/(1+eps) is the arg-max of (P - c) P^eps (first-order condition)."""
    c = 100.0
    p_star = pm.lerner_optimal_price(c, eps)
    grid = np.linspace(c * 1.001, c * 12, 200_000)
    profit = (grid - c) * grid ** eps
    assert abs(grid[np.argmax(profit)] - p_star) / p_star < 1e-3
    # Inverse-elasticity rule: (P* - c)/P* = -1/eps
    assert pm.lerner_index(p_star, c) == pytest.approx(-1.0 / eps, rel=1e-12)


def test_inelastic_demand_is_a_corner_solution():
    p, regime = pm.unconstrained_optimum(100.0, -0.7, 250.0)
    assert (p, regime) == (250.0, pm.REGIME_INELASTIC)
    with pytest.raises(ValueError):
        pm.lerner_optimal_price(100.0, -0.9)


def test_markup_and_gross_margin_are_inverse():
    for m in (0.0, 0.2, 0.25, 0.5, 1.0, 3.0):
        g = pm.markup_to_gross_margin(m)
        assert pm.gross_margin_to_markup(g) == pytest.approx(m)
    # The 22% "margin floor" on the GPU SKU is a markup: it implies 18.03% gross margin.
    assert pm.markup_to_gross_margin(0.22) == pytest.approx(0.180327, rel=1e-5)


def test_guardrail_conflict_is_rejected():
    with pytest.raises(pm.GuardrailConfigError):
        pm.clamp_to_guardrails(100.0, floor=120.0, ceiling=110.0)
    assert pm.clamp_to_guardrails(float("nan"), 10.0, 20.0) == (10.0, pm.CLAMPED_TO_MARGIN_FLOOR)
    assert pm.clamp_to_guardrails(float("inf"), 10.0, 20.0) == (20.0, pm.CLAMPED_TO_MSRP_CEILING)


def test_guardrail_invariant_fuzz_all_skus():
    """FR-03: floor <= P_rec <= MSRP for 3,000 random (adversarial) input combinations."""
    rng = random.Random(42)
    for _ in range(3000):
        tenant_id, sku_id = rng.choice(ALL_SKUS)
        q = elasticity_service.compute_guarded_quote(
            tenant_id, sku_id,
            inventory_level=rng.choice([0, 1, 2, rng.randint(0, 1_000_000)]),
            competitor_price=rng.choice([None, 0.01, 1e6, rng.uniform(0.01, 1e6)]),
            demand_surge_factor=rng.choice([0.1, 10.0, rng.uniform(0.1, 10.0)]),
        )
        assert q["margin_floor_price"] <= q["recommended_price"] <= q["max_msrp_ceiling"]
        assert q["recommended_price"] >= q["unit_cogs"] * (1 + q["min_margin_pct"]) - 0.01


# ---------------------------------------------------------------------------
# Inventory sell-through & price-change economics
# ---------------------------------------------------------------------------
def test_sell_through_price_hits_target_days_of_cover():
    eps, p0, q0, inv = -2.5, 100.0, 50.0, 100.0  # DoC = 2 days < 7 -> scarcity
    p, regime, doc = pm.inventory_adjusted_price(p0, q0, inv, eps, 7.0, 45.0, 1_000.0)
    assert regime == pm.INVENTORY_SCARCITY and doc == pytest.approx(2.0) and p > p0
    q_new = q0 * (p / p0) ** eps
    assert inv / q_new == pytest.approx(7.0)

    p, regime, _ = pm.inventory_adjusted_price(p0, q0, 10_000.0, eps, 7.0, 45.0, 1_000.0)
    assert regime == pm.INVENTORY_OVERSTOCK and p < p0
    assert 10_000.0 / (q0 * (p / p0) ** eps) == pytest.approx(45.0)

    assert pm.inventory_adjusted_price(p0, q0, 0, eps, 7.0, 45.0, 1_000.0)[1] == pm.INVENTORY_STOCKOUT


def test_quote_reacts_to_scarcity_and_overstock():
    lo = elasticity_service.compute_guarded_quote("tenant_alpha", "SKU-NET-SWITCH-400G", inventory_level=20)
    mid = elasticity_service.compute_guarded_quote("tenant_alpha", "SKU-NET-SWITCH-400G", inventory_level=900)
    hi = elasticity_service.compute_guarded_quote("tenant_alpha", "SKU-NET-SWITCH-400G", inventory_level=200_000)
    assert lo["recommended_price"] > mid["recommended_price"] > hi["recommended_price"]
    assert lo["inventory_regime"] == pm.INVENTORY_SCARCITY
    assert mid["inventory_regime"] == pm.INVENTORY_BALANCED
    assert hi["guardrail_status"] == pm.CLAMPED_TO_MARGIN_FLOOR


def test_profit_uplift_closed_form_matches_direct_computation():
    c, p0, p1, eps, k = 60.0, 100.0, 112.0, -2.3, 7.7
    q = lambda p: k * p ** eps  # noqa: E731
    direct = ((p1 - c) * q(p1)) / ((p0 - c) * q(p0)) - 1.0
    assert pm.profit_uplift_pct(p1, p0, c, eps) == pytest.approx(direct, rel=1e-12)


def test_breakeven_volume_change_keeps_profit_constant():
    c, p0 = 60.0, 100.0
    for dp in (-0.15, -0.05, 0.05, 0.2):
        dq = pm.breakeven_volume_change(dp, (p0 - c) / p0)
        assert (p0 * (1 + dp) - c) * (1 + dq) == pytest.approx(p0 - c)


def test_arc_elasticity_matches_constant_elasticity_for_small_changes():
    eps = -2.2
    q = lambda p: 1000 * p ** eps  # noqa: E731
    assert pm.arc_elasticity(q(100), q(100.5), 100, 100.5) == pytest.approx(eps, rel=1e-3)


# ---------------------------------------------------------------------------
# Econometrics
# ---------------------------------------------------------------------------
def test_ols_recovers_known_elasticity_with_valid_inference():
    rng = np.random.default_rng(11)
    n, eps, beta, alpha = 720, -2.4, 0.4, 9.0
    p = 100 * rng.uniform(0.8, 1.2, n)
    pc = 100 * rng.uniform(0.9, 1.1, n)
    dow = np.arange(n) % 7
    gamma = np.array([0.0, 0.05, 0.1, 0.1, 0.05, -0.2, -0.3])
    lnq = alpha + eps * np.log(p) + beta * np.log(pc) + gamma[dow] + rng.normal(0, 0.1, n)
    fit = fit_log_log_ols(p, pc, np.exp(lnq), dow)
    assert fit["ci95_epsilon_low"] < eps < fit["ci95_epsilon_high"]
    assert fit["elasticity_epsilon"] == pytest.approx(eps, abs=0.1)
    assert fit["cross_elasticity_beta"] == pytest.approx(beta, abs=0.25)
    assert fit["p_value_epsilon"] < 1e-10
    assert 1.6 < fit["durbin_watson"] < 2.4
    assert fit["adj_r_squared"] <= fit["r_squared"]
    # Duan smearing ~ exp(sigma^2 / 2) for normal errors and always >= 1 (Jensen)
    assert fit["smearing_factor"] == pytest.approx(math.exp(0.1 ** 2 / 2), rel=0.01)
    assert fit["smearing_factor"] >= 1.0


def test_seeded_lakehouse_elasticity_is_identified():
    for tenant_id, sku_id in ALL_SKUS:
        fit = elasticity_service.fit_sku_elasticity(tenant_id, sku_id)
        assert fit["elasticity_epsilon"] < -1.0 and not fit["is_prior_fallback"]
        assert fit["ci95_epsilon_high"] < -1.0, "elasticity must be significantly elastic"
        assert fit["r_squared"] > 0.5
        assert fit["vif_log_price"] < 5.0


def test_bandit_demand_is_on_the_right_scale():
    """Regression for the v1 intercept bug (demand of ~1e7 units/day)."""
    res = bandit_service.run_simulation("tenant_alpha", "SKU-GPU-H100", horizon=200, seed=1)
    per_round = res["bandit_profit"] / res["horizon"]
    quote = elasticity_service.compute_guarded_quote("tenant_alpha", "SKU-GPU-H100", 400)
    assert 0.3 < per_round / quote["expected_daily_profit_usd"] < 3.0


# ---------------------------------------------------------------------------
# Bandits
# ---------------------------------------------------------------------------
def test_bandits_are_reproducible_with_seed():
    a = bandit_service.run_simulation("tenant_beta", "SKU-COLD-REEFER", horizon=300, algorithm="ucb1", seed=99)
    b = bandit_service.run_simulation("tenant_beta", "SKU-COLD-REEFER", horizon=300, algorithm="ucb1", seed=99)
    assert a == b


# UCB1's deterministic optimism explores longer than Thompson Sampling under heavy
# Poisson noise, so its regret reduction at T=2000 is smaller (but still positive).
@pytest.mark.parametrize("algorithm,min_reduction", [("thompson_sampling", 50.0), ("ucb1", 5.0), ("epsilon_greedy", 50.0)])
def test_bandits_beat_static_regret(algorithm, min_reduction):
    res = bandit_service.run_simulation("tenant_alpha", "SKU-GPU-H100", horizon=2000, algorithm=algorithm, seed=7)
    assert res["cumulative_regret"] < res["static_cumulative_regret"]
    assert res["regret_reduction_pct"] > min_reduction
    assert sum(res["arm_allocations"].values()) == 2000
    # The most-pulled arm is (near-)optimal in true expected profit.
    top = max(res["arm_allocations"], key=res["arm_allocations"].get)
    mu = res["arm_true_expected_profit"]
    assert mu[top] >= 0.97 * max(mu.values())


def test_thompson_sampling_regret_is_sublinear():
    rng = np.random.default_rng(3)
    arms, lam = [80.0, 100.0, 120.0], [30.0, 22.0, 14.0]
    sim = run_bandit(arms, lam, 50.0, 100.0, 22.0, 4000, "thompson_sampling", 0.1, rng)
    hist = {h["step"]: h["cum_regret"] for h in sim["history"]}
    early, late = hist[400], hist[4000] - hist[3600]
    assert late < early, "regret growth must slow down as the posterior concentrates"


# ---------------------------------------------------------------------------
# Unit economics
# ---------------------------------------------------------------------------
def test_discounted_ltv_closed_form_matches_series():
    m, c, d = 1_000.0, 0.03, ue.monthly_discount_rate(0.10)
    series = sum(m * (1 - c) ** (t - 1) / (1 + d) ** t for t in range(1, 5000))
    assert ue.calculate_discounted_ltv(1_000.0, 1.0, c, d) == pytest.approx(series, abs=0.01)
    assert ue.calculate_discounted_ltv(2_000.0, 0.5, 0.05, 0.0) == ue.calculate_ltv(2_000.0, 0.5, 0.05)
    assert (1 + d) ** 12 == pytest.approx(1.10)


def test_churn_adjusted_payback():
    arpu, gm, c, cac = 2_000.0, 0.5, 0.05, 4_000.0
    n = ue.calculate_churn_adjusted_payback(cac, arpu, gm, c)
    m = arpu * gm
    assert m * (1 - (1 - c) ** n) / c == pytest.approx(cac, rel=1e-3)
    assert n >= ue.calculate_payback_months(cac, arpu, gm)
    # Never pays back when CAC >= LTV
    assert ue.calculate_churn_adjusted_payback(25_000.0, arpu, gm, c) is None


def test_churn_lifetime_identities():
    c = 0.04
    assert ue.expected_lifetime_months(c) == pytest.approx(25.0)
    assert ue.annual_churn_rate(c) == pytest.approx(1 - 0.96 ** 12)
    assert (1 - c) ** ue.customer_half_life_months(c) == pytest.approx(0.5)


def test_cohort_aggregates_are_customer_weighted():
    data = ue.get_tenant_cohorts("tenant_alpha")
    rows = data["cohorts"]
    n = sum(r["new_customers"] for r in rows)
    ltv = sum(r["new_customers"] * r["ltv_usd"] for r in rows) / n
    cac = sum(r["new_customers"] * r["cac_usd"] for r in rows) / n
    assert data["aggregate_ltv_to_cac"] == pytest.approx(ltv / cac, abs=0.01)
    assert all(r["discounted_ltv_usd"] < r["ltv_usd"] for r in rows)


# ---------------------------------------------------------------------------
# Data quality & latency
# ---------------------------------------------------------------------------
def test_quality_gate_is_idempotent_and_reports_reasons():
    first = lakehouse.run_anti_poisoning_quality_gate("tenant_beta")
    second = lakehouse.run_anti_poisoning_quality_gate("tenant_beta")
    assert first["rejected_poison_count"] == second["rejected_poison_count"] == 4
    assert set(first["rejection_reasons"]) <= {"PRICE_ROBUST_Z_GT_TOL", "VOLUME_ROBUST_Z_GT_TOL",
                                               "COMPETITOR_PRICE_ROBUST_Z_GT_TOL", "NON_POSITIVE_VALUE"}


def test_quality_gate_fails_closed_on_mass_poisoning():
    """A batch where >20% of events are poisoned must not be promoted to Silver."""
    tenant = "tenant_gamma_poison"
    with lakehouse.get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO sku_catalog VALUES ('SKU-X', ?, 'X', 10, 0.2, 30, 20)", [tenant])
        rows = []
        for i in range(100):
            poisoned = i % 3 == 0
            rows.append((f"g{i}", tenant, "SKU-X", f"2026-01-{i % 28 + 1:02d}", 2000.0 if poisoned else 20.0 + i % 5,
                         20.0, 10, 100, False, None))
        conn.executemany("INSERT INTO bronze_market_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    try:
        res = lakehouse.run_anti_poisoning_quality_gate(tenant)
        assert res["status"] == "FAILED_QUARANTINE_RATIO"
        assert res["passed_silver_buckets"] == 0
    finally:
        with lakehouse.get_connection() as conn:
            for table in ("bronze_market_events", "silver_validated_demand", "sku_catalog", "audit_quality_gates"):
                conn.execute(f"DELETE FROM {table} WHERE tenant_id = ?", [tenant])


def test_quote_p95_latency_under_50ms():
    """NFR-01: P95 latency for guarded quotes stays under 50ms."""
    timings = []
    for i in range(300):
        tenant_id, sku_id = ALL_SKUS[i % len(ALL_SKUS)]
        t0 = time.perf_counter()
        elasticity_service.compute_guarded_quote(tenant_id, sku_id, inventory_level=400, competitor_price=None)
        timings.append((time.perf_counter() - t0) * 1000)
    assert statistics.quantiles(timings, n=20)[-1] < 50.0
