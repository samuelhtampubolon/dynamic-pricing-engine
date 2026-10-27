"""Multi-Armed Bandit Price Experimentation Simulator (Thompson Sampling & Epsilon-Greedy)."""

from __future__ import annotations
import math
from typing import Any, Dict, List, Optional
import numpy as np

from src.lakehouse import lakehouse
from src.services.elasticity_model import elasticity_service


class BanditSimulatorService:
    """Simulates dynamic pricing experimentation using Thompson Sampling and Epsilon-Greedy

    against a static price baseline under econometric demand curves.
    """

    def run_simulation(
        self,
        tenant_id: str,
        sku_id: str,
        horizon: int = 500,
        algorithm: str = "thompson_sampling",
        epsilon_param: float = 0.1,
        candidate_prices: Optional[List[float]] = None,
    ) -> Dict[str, Any]:
        """Run A/B/n price experimentation simulation for specified horizon (capped at 5000)."""
        # Enforce API4 resource consumption safety cap
        horizon = min(5000, max(10, horizon))

        # Retrieve SKU specs
        with lakehouse.get_connection(read_only=True) as conn:
            sku_row = conn.execute("""
                SELECT unit_cogs, min_margin_pct, max_msrp, base_price
                FROM sku_catalog
                WHERE tenant_id = ? AND sku_id = ?
            """, [tenant_id, sku_id]).fetchone()

        if not sku_row:
            raise ValueError(f"SKU '{sku_id}' not found for tenant '{tenant_id}'.")

        unit_cogs, min_margin_pct, max_msrp, base_price = sku_row
        margin_floor = unit_cogs * (1.0 + min_margin_pct)

        # Retrieve econometric parameters
        fit = elasticity_service.fit_sku_elasticity(tenant_id, sku_id)
        true_elasticity = fit["elasticity_epsilon"]
        alpha = fit.get("alpha", 8.0)

        # Build candidate arms (prices)
        if not candidate_prices:
            # Generate 5 reasonable price arms around base price within guardrails
            multipliers = [0.85, 0.95, 1.00, 1.08, 1.18]
            arms = [
                round(max(margin_floor, min(max_msrp, base_price * m)), 2)
                for m in multipliers
            ]
            arms = sorted(list(set(arms)))
            if len(arms) < 2:
                arms = [margin_floor, round((margin_floor + max_msrp) / 2, 2), max_msrp]
        else:
            # Clamp user provided arms to guardrails
            arms = [
                round(max(margin_floor, min(max_msrp, p)), 2)
                for p in candidate_prices
            ]
            arms = sorted(list(set(arms)))

        k_arms = len(arms)
        static_price = base_price

        # Demand function: Q(P) = exp(alpha + epsilon * ln(P/base_price) - 0.5)
        def sample_demand(p: float) -> float:
            log_price_ratio = math.log(p / base_price)
            expected_q = math.exp(alpha + true_elasticity * log_price_ratio)
            # Add stochastic Poisson/Normal market shock
            noise = np.random.normal(0, max(1.0, expected_q * 0.12))
            return max(0.0, expected_q + noise)

        # Identify theoretical optimal arm for regret tracking
        expected_arm_profits = []
        for p in arms:
            log_ratio = math.log(p / base_price)
            exp_q = math.exp(alpha + true_elasticity * log_ratio)
            expected_arm_profits.append((p - unit_cogs) * exp_q)
        optimal_expected_profit = max(expected_arm_profits)

        # Bandit state
        arm_counts = np.zeros(k_arms, dtype=int)
        arm_rewards_sum = np.zeros(k_arms, dtype=float)

        # Thompson Sampling Gaussian Priors (mean, variance)
        prior_means = np.array([expected_arm_profits[i] * 0.8 for i in range(k_arms)])
        prior_vars = np.ones(k_arms) * (optimal_expected_profit * 0.5) ** 2

        cum_bandit_rev = 0.0
        cum_bandit_profit = 0.0
        cum_static_rev = 0.0
        cum_static_profit = 0.0
        cum_regret = 0.0

        history = []
        sample_interval = max(1, horizon // 40)

        for t in range(1, horizon + 1):
            # Select arm
            if algorithm == "thompson_sampling":
                # Sample from posterior distribution of expected profit for each arm
                sampled_values = np.random.normal(prior_means, np.sqrt(prior_vars))
                chosen_idx = int(np.argmax(sampled_values))
            else:
                # Epsilon-Greedy
                if np.random.random() < epsilon_param or t <= k_arms:
                    chosen_idx = int(np.random.randint(0, k_arms))
                else:
                    avg_rewards = np.divide(
                        arm_rewards_sum,
                        np.maximum(1, arm_counts),
                    )
                    chosen_idx = int(np.argmax(avg_rewards))

            chosen_price = arms[chosen_idx]

            # Simulate demand for chosen price & static baseline
            q_bandit = sample_demand(chosen_price)
            rev_bandit = chosen_price * q_bandit
            profit_bandit = (chosen_price - unit_cogs) * q_bandit

            q_static = sample_demand(static_price)
            rev_static = static_price * q_static
            profit_static = (static_price - unit_cogs) * q_static

            # Update Bandit state
            arm_counts[chosen_idx] += 1
            arm_rewards_sum[chosen_idx] += profit_bandit

            # Update Bayesian posterior for Thompson Sampling
            n = arm_counts[chosen_idx]
            prior_vars[chosen_idx] = prior_vars[chosen_idx] / (1.0 + 0.05 * n)
            prior_means[chosen_idx] = (
                (prior_means[chosen_idx] * (n - 1) + profit_bandit) / n
            )

            # Accumulate totals
            cum_bandit_rev += rev_bandit
            cum_bandit_profit += profit_bandit
            cum_static_rev += rev_static
            cum_static_profit += profit_static
            cum_regret += max(0.0, optimal_expected_profit - expected_arm_profits[chosen_idx])

            if t % sample_interval == 0 or t == horizon:
                history.append({
                    "step": t,
                    "cum_bandit_profit": round(cum_bandit_profit, 2),
                    "cum_static_profit": round(cum_static_profit, 2),
                    "cum_regret": round(cum_regret, 2),
                    "bandit_uplift_pct": round(
                        ((cum_bandit_profit - cum_static_profit) / max(1.0, cum_static_profit)) * 100.0,
                        2,
                    ),
                    "last_price": chosen_price,
                })

        profit_uplift_pct = (
            ((cum_bandit_profit - cum_static_profit) / max(1.0, cum_static_profit)) * 100.0
        )
        revenue_uplift_pct = (
            ((cum_bandit_rev - cum_static_rev) / max(1.0, cum_static_rev)) * 100.0
        )

        allocations = {f"${arms[i]:.2f}": int(arm_counts[i]) for i in range(k_arms)}
        estimated_values = {
            f"${arms[i]:.2f}": round(float(arm_rewards_sum[i] / max(1, arm_counts[i])), 2)
            for i in range(k_arms)
        }

        return {
            "tenant_id": tenant_id,
            "sku_id": sku_id,
            "algorithm": algorithm,
            "horizon": horizon,
            "baseline_revenue": round(cum_static_rev, 2),
            "baseline_profit": round(cum_static_profit, 2),
            "bandit_revenue": round(cum_bandit_rev, 2),
            "bandit_profit": round(cum_bandit_profit, 2),
            "cumulative_regret": round(cum_regret, 2),
            "profit_uplift_pct": round(profit_uplift_pct, 2),
            "revenue_uplift_pct": round(revenue_uplift_pct, 2),
            "arm_allocations": allocations,
            "arm_estimated_values": estimated_values,
            "step_history": history,
        }


bandit_service = BanditSimulatorService()
