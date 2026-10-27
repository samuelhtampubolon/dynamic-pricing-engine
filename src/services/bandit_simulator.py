"""Multi-Armed Bandit Price Experimentation Simulator (Thompson Sampling, Epsilon-Greedy, UCB1)."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import numpy as np

from src.config import settings
from src.lakehouse import lakehouse
from src.services import pricing_math as pm
from src.services.elasticity_model import SkuNotFoundError, elasticity_service

DEFAULT_ARM_MULTIPLIERS = (0.85, 0.95, 1.00, 1.08, 1.18)


def run_bandit(
    arms: List[float],
    expected_units: List[float],
    unit_cogs: float,
    baseline_price: float,
    baseline_units: float,
    horizon: int,
    algorithm: str,
    epsilon_param: float,
    rng: np.random.Generator,
    include_history: bool = True,
) -> Dict[str, Any]:
    """Pure simulation kernel (no I/O).

    Each round is one trading day. Demand is Poisson(lambda(P)) with lambda from the
    calibrated log-log model; the reward is gross profit (P - c) * Q.

    * Thompson Sampling (Gaussian, flat prior): theta_i ~ N(xbar_i, s^2 / n_i)
    * UCB1 (scaled):  xbar_i + s * sqrt(2 ln t / n_i)
    * Epsilon-greedy: explore with probability eps, else argmax xbar_i
    All policies pull every arm once first (round-robin initialisation).
    Pseudo-regret R_T = sum_t (mu* - mu_{a_t}), mu_i = (P_i - c) * lambda_i.
    """
    k = len(arms)
    prices = np.asarray(arms, dtype=np.float64)
    lam = np.asarray(expected_units, dtype=np.float64)
    mu = (prices - unit_cogs) * lam
    best = int(np.argmax(mu))
    mu_star = float(mu[best])
    mu_static = (baseline_price - unit_cogs) * baseline_units

    counts = np.zeros(k, dtype=np.int64)
    means = np.zeros(k)
    m2 = np.zeros(k)  # Welford sum of squared deviations

    cum_rev = cum_profit = cum_static_rev = cum_static_profit = 0.0
    cum_regret = 0.0
    history: List[Dict[str, Any]] = []
    sample_interval = max(1, horizon // 50)

    for t in range(1, horizon + 1):
        if t <= k:
            idx = t - 1
        else:
            pooled_var = float(m2.sum() / max(1, counts.sum() - k))
            s = math.sqrt(max(pooled_var, 1e-9))
            if algorithm == "thompson_sampling":
                idx = int(np.argmax(rng.normal(means, s / np.sqrt(counts))))
            elif algorithm == "ucb1":
                idx = int(np.argmax(means + s * np.sqrt(2.0 * math.log(t) / counts)))
            else:  # epsilon_greedy
                idx = int(rng.integers(0, k)) if rng.random() < epsilon_param else int(np.argmax(means))

        q = float(rng.poisson(lam[idx]))
        reward = (prices[idx] - unit_cogs) * q
        counts[idx] += 1
        delta = reward - means[idx]
        means[idx] += delta / counts[idx]
        m2[idx] += delta * (reward - means[idx])

        q_static = float(rng.poisson(baseline_units))
        cum_rev += prices[idx] * q
        cum_profit += reward
        cum_static_rev += baseline_price * q_static
        cum_static_profit += (baseline_price - unit_cogs) * q_static
        cum_regret += mu_star - mu[idx]

        if include_history and (t % sample_interval == 0 or t == horizon):
            history.append({
                "step": t,
                "cum_bandit_profit": round(float(cum_profit), 2),
                "cum_static_profit": round(cum_static_profit, 2),
                "cum_regret": round(float(cum_regret), 2),
                "cum_static_regret": round(float(t * (mu_star - mu_static)), 2),
                "bandit_uplift_pct": round(float(cum_profit - cum_static_profit) / max(1.0, abs(cum_static_profit)) * 100.0, 2),
                "last_price": float(prices[idx]),
            })

    static_regret = horizon * (mu_star - mu_static)
    return {
        "counts": counts,
        "means": means,
        "mu": mu,
        "best": best,
        "cum_rev": cum_rev,
        "cum_profit": cum_profit,
        "cum_static_rev": cum_static_rev,
        "cum_static_profit": cum_static_profit,
        "cum_regret": cum_regret,
        "static_regret": static_regret,
        "history": history,
    }


class BanditSimulatorService:
    """Simulates dynamic pricing experimentation against a static price baseline
    under the tenant's calibrated econometric demand curve.
    """

    def run_simulation(
        self,
        tenant_id: str,
        sku_id: str,
        horizon: int = 500,
        algorithm: str = "thompson_sampling",
        epsilon_param: float = 0.1,
        candidate_prices: Optional[List[float]] = None,
        seed: Optional[int] = None,
        include_history: bool = True,
    ) -> Dict[str, Any]:
        """Run an A/B/n price experiment for ``horizon`` rounds (capped at MAX_SIMULATION_HORIZON)."""
        if algorithm not in {"thompson_sampling", "epsilon_greedy", "ucb1"}:
            raise ValueError("Unsupported bandit algorithm.")
        horizon = int(min(settings.MAX_SIMULATION_HORIZON, max(10, horizon)))
        epsilon_param = float(min(1.0, max(0.0, epsilon_param)))

        with lakehouse.get_connection(read_only=True) as conn:
            sku_row = conn.execute("""
                SELECT unit_cogs, min_margin_pct, max_msrp, base_price
                FROM sku_catalog
                WHERE tenant_id = ? AND sku_id = ?
            """, [tenant_id, sku_id]).fetchone()
        if not sku_row:
            raise SkuNotFoundError(f"SKU '{sku_id}' not found for the authenticated tenant.")

        unit_cogs, min_margin_pct, max_msrp, base_price = sku_row
        floor = pm.margin_floor_price(unit_cogs, min_margin_pct)

        fit = elasticity_service.fit_sku_elasticity(tenant_id, sku_id)
        eps = fit["elasticity_epsilon"] if fit["elasticity_epsilon"] < 0 else settings.PRIOR_ELASTICITY

        def lam(price: float) -> float:
            return pm.expected_demand(fit["alpha"], eps, fit["cross_elasticity_beta"], price,
                                      base_price, 1.0, fit["smearing_factor"])

        # Candidate arms are always clamped into the guardrail envelope.
        if candidate_prices:
            raw_arms = [p for p in candidate_prices if math.isfinite(p) and p > 0]
        else:
            # Default grid around the static price plus the Lerner optimum; grid points
            # within 1% of P* are dropped so the arena never contains near-duplicate arms.
            p_star = fit["raw_optimal_price"]
            raw_arms = [base_price * m for m in DEFAULT_ARM_MULTIPLIERS if abs(base_price * m / p_star - 1.0) > 0.01]
            raw_arms.append(p_star)
        arms = sorted({round(pm.clamp_to_guardrails(p, floor, max_msrp)[0], 2) for p in raw_arms})
        if len(arms) < 2:
            arms = sorted({round(floor, 2), round((floor + max_msrp) / 2.0, 2), round(max_msrp, 2)})

        rng = np.random.default_rng(seed)
        sim = run_bandit(
            arms=arms,
            expected_units=[lam(p) for p in arms],
            unit_cogs=unit_cogs,
            baseline_price=base_price,
            baseline_units=lam(base_price),
            horizon=horizon,
            algorithm=algorithm,
            epsilon_param=epsilon_param,
            rng=rng,
            include_history=include_history,
        )

        base_profit = sim["cum_static_profit"]
        base_rev = sim["cum_static_rev"]
        labels = [f"${p:,.2f}" for p in arms]
        static_regret = sim["static_regret"]
        return {
            "tenant_id": tenant_id,
            "sku_id": sku_id,
            "algorithm": algorithm,
            "horizon": horizon,
            "baseline_price": round(base_price, 2),
            "baseline_revenue": round(base_rev, 2),
            "baseline_profit": round(base_profit, 2),
            "bandit_revenue": round(float(sim["cum_rev"]), 2),
            "bandit_profit": round(float(sim["cum_profit"]), 2),
            "cumulative_regret": round(float(sim["cum_regret"]), 2),
            "static_cumulative_regret": round(float(static_regret), 2),
            "regret_reduction_pct": round(float(1.0 - sim["cum_regret"] / static_regret) * 100.0, 2) if static_regret > 0 else 0.0,
            "profit_uplift_pct": round(float(sim["cum_profit"] - base_profit) / max(1.0, abs(base_profit)) * 100.0, 2),
            "revenue_uplift_pct": round(float(sim["cum_rev"] - base_rev) / max(1.0, abs(base_rev)) * 100.0, 2),
            "optimal_arm_price": float(arms[sim["best"]]),
            "optimal_arm_share": round(float(sim["counts"][sim["best"]]) / horizon, 4),
            "arm_allocations": {labels[i]: int(sim["counts"][i]) for i in range(len(arms))},
            "arm_estimated_values": {labels[i]: round(float(sim["means"][i]), 2) for i in range(len(arms))},
            "arm_true_expected_profit": {labels[i]: round(float(sim["mu"][i]), 2) for i in range(len(arms))},
            "step_history": sim["history"] if include_history else None,
        }


bandit_service = BanditSimulatorService()
