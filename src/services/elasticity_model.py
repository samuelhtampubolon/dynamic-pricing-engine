"""Econometric Price Elasticity Model (Log-Log OLS) and Guarded Pricing Optimizer."""

from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
from scipy import stats
from sklearn.linear_model import LinearRegression

from src.config import settings
from src.lakehouse import lakehouse
from src.services import pricing_math as pm

MIN_OLS_OBSERVATIONS = 12


class SkuNotFoundError(LookupError):
    """Raised when a SKU does not exist inside the caller's tenant scope."""


def fit_log_log_ols(
    prices: Sequence[float],
    competitor_prices: Sequence[float],
    quantities: Sequence[float],
    day_of_week: Optional[Sequence[int]] = None,
) -> Dict[str, Any]:
    """Estimate ln Q = alpha + eps ln P + beta ln P_comp + sum_d gamma_d DOW_d + u.

    Returns coefficients with classical OLS inference:
      * Var(b) = s^2 (X'X)^-1,  s^2 = RSS / (n - k)
      * t = b / se, two-sided p-value from Student-t(n - k)
      * 95% CI = b +/- t_{0.975, n-k} * se
      * adj R^2 = 1 - (1 - R^2)(n - 1)/(n - k)
      * Durbin-Watson d = sum (e_t - e_{t-1})^2 / sum e_t^2
      * VIF(ln P) = 1 / (1 - R^2_aux) from regressing ln P on the other regressors
      * Duan smearing factor S = mean(exp(e)) for unbiased retransformation of E[Q]
      * AIC = n ln(RSS/n) + 2k,  BIC = n ln(RSS/n) + k ln n
    ``alpha`` is reported as the average-day intercept, i.e. the day-of-week
    effects are absorbed: alpha_avg = alpha + ln(mean_d exp(gamma_d)).
    """
    p = np.asarray(prices, dtype=np.float64)
    pc = np.asarray(competitor_prices, dtype=np.float64)
    q = np.asarray(quantities, dtype=np.float64)
    mask = (p > 0) & (pc > 0) & (q > 0) & np.isfinite(p) & np.isfinite(pc) & np.isfinite(q)
    p, pc, q = p[mask], pc[mask], q[mask]
    n = int(p.size)

    regressors = [np.log(p), np.log(pc)]
    dow_cols: List[int] = []
    if day_of_week is not None:
        dow = np.asarray(day_of_week, dtype=int)[mask]
        for d in range(1, 7):
            col = (dow == d).astype(np.float64)
            if col.any():
                regressors.append(col)
                dow_cols.append(d)
    X = np.column_stack(regressors)
    y = np.log(q)
    k = X.shape[1] + 1  # + intercept
    if n <= k + 2:
        raise ValueError("Not enough observations for OLS inference.")

    model = LinearRegression().fit(X, y)
    resid = y - model.predict(X)
    rss = float(resid @ resid)
    tss = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - rss / tss if tss > 0 else 0.0
    dof = n - k
    if tss <= 0:
        raise ValueError("Demand has no variation; elasticity is not identified.")
    s2 = rss / dof

    Xd = np.column_stack([np.ones(n), X])
    xtx_inv = np.linalg.pinv(Xd.T @ Xd)
    se = np.sqrt(np.maximum(np.diag(xtx_inv) * s2, 0.0))
    coefs = np.concatenate([[model.intercept_], model.coef_])
    t_crit = float(stats.t.ppf(0.975, dof))

    def _t_and_p(idx: int) -> tuple[float, float]:
        t_val = float(coefs[idx] / se[idx]) if se[idx] > 0 else float("inf")
        return t_val, float(2.0 * stats.t.sf(abs(t_val), dof))

    t_eps, p_eps = _t_and_p(1)

    # VIF for ln P (auxiliary regression on the remaining regressors)
    others = np.column_stack([np.ones(n), X[:, 1:]])
    aux_beta, *_ = np.linalg.lstsq(others, X[:, 0], rcond=None)
    aux_resid = X[:, 0] - others @ aux_beta
    aux_tss = float(((X[:, 0] - X[:, 0].mean()) ** 2).sum())
    aux_r2 = 1.0 - float(aux_resid @ aux_resid) / aux_tss if aux_tss > 0 else 0.0
    vif = 1.0 / max(1e-12, 1.0 - aux_r2)

    dow_effects = {int(d): float(model.coef_[2 + i]) for i, d in enumerate(dow_cols)}
    gammas = np.array([0.0] + [dow_effects.get(d, 0.0) for d in range(1, 7)]) if dow_cols else np.array([0.0])
    alpha_avg = float(model.intercept_) + float(np.log(np.mean(np.exp(gammas))))

    return {
        "elasticity_epsilon": float(coefs[1]),
        "cross_elasticity_beta": float(coefs[2]),
        "alpha": alpha_avg,
        "alpha_monday": float(model.intercept_),
        "dow_effects": dow_effects,
        "r_squared": max(0.0, min(1.0, r2)),
        "adj_r_squared": 1.0 - (1.0 - r2) * (n - 1) / dof,
        "rmse_log": math.sqrt(rss / n),
        "std_error_epsilon": float(se[1]),
        "t_stat_epsilon": t_eps,
        "p_value_epsilon": p_eps,
        "ci95_epsilon_low": float(coefs[1] - t_crit * se[1]),
        "ci95_epsilon_high": float(coefs[1] + t_crit * se[1]),
        "std_error_beta": float(se[2]),
        "durbin_watson": float(np.sum(np.diff(resid) ** 2) / rss) if rss > 0 else 2.0,
        "vif_log_price": vif,
        "smearing_factor": float(np.mean(np.exp(resid))),
        "aic": n * math.log(max(rss, 1e-12) / n) + 2 * k,
        "bic": n * math.log(max(rss, 1e-12) / n) + k * math.log(n),
        "sample_size": n,
        "is_prior_fallback": False,
    }


class ElasticityModelService:
    """Estimates price elasticity of demand via log-log OLS regression

    and computes profit-maximizing prices subject to hard margin floor & MSRP guardrails.
    """

    def _get_sku(self, tenant_id: str, sku_id: str) -> Dict[str, Any]:
        with lakehouse.get_connection(read_only=True) as conn:
            row = conn.execute("""
                SELECT sku_name, unit_cogs, min_margin_pct, max_msrp, base_price
                FROM sku_catalog
                WHERE tenant_id = ? AND sku_id = ?
            """, [tenant_id, sku_id]).fetchone()
        if not row:
            raise SkuNotFoundError(f"SKU '{sku_id}' not found for the authenticated tenant.")
        return dict(zip(["sku_name", "unit_cogs", "min_margin_pct", "max_msrp", "base_price"], row))

    def _prior_fit(self, tenant_id: str, sku_id: str, n: int, base_price: float, mean_log_q: float) -> Dict[str, Any]:
        eps = settings.PRIOR_ELASTICITY
        return {
            "elasticity_epsilon": eps,
            "cross_elasticity_beta": 0.0,
            # Calibrate the intercept so that Q(base_price) matches observed mean demand.
            "alpha": mean_log_q - eps * math.log(base_price),
            "alpha_monday": mean_log_q - eps * math.log(base_price),
            "dow_effects": {},
            "r_squared": 0.0,
            "adj_r_squared": 0.0,
            "rmse_log": 0.0,
            "std_error_epsilon": 0.0,
            "t_stat_epsilon": 0.0,
            "p_value_epsilon": 1.0,
            "ci95_epsilon_low": eps,
            "ci95_epsilon_high": eps,
            "std_error_beta": 0.0,
            "durbin_watson": 2.0,
            "vif_log_price": 1.0,
            "smearing_factor": 1.0,
            "aic": 0.0,
            "bic": 0.0,
            "sample_size": n,
            "is_prior_fallback": True,
        }

    def fit_sku_elasticity(self, tenant_id: str, sku_id: str) -> Dict[str, Any]:
        """Fit the log-log OLS model on silver_validated_demand for one SKU.

        Equation: ln(Q) = alpha + epsilon * ln(P) + beta * ln(P_comp) + DOW effects
        """
        sku = self._get_sku(tenant_id, sku_id)
        with lakehouse.get_connection(read_only=True) as conn:
            rows = conn.execute("""
                SELECT avg_price, avg_comp_price, total_qty, date_bucket
                FROM silver_validated_demand
                WHERE tenant_id = ? AND sku_id = ? AND total_qty > 0 AND avg_price > 0
                ORDER BY date_bucket
            """, [tenant_id, sku_id]).fetchall()

        fit: Optional[Dict[str, Any]] = None
        if len(rows) >= MIN_OLS_OBSERVATIONS:
            dates: List[date] = [r[3] for r in rows]
            try:
                fit = fit_log_log_ols(
                    [r[0] for r in rows],
                    [r[1] if r[1] and r[1] > 0 else r[0] for r in rows],
                    [r[2] for r in rows],
                    [d.weekday() for d in dates],
                )
            except (ValueError, np.linalg.LinAlgError):
                fit = None  # not identified: fall back to the documented prior
        if fit is None:
            mean_log_q = float(np.mean(np.log([r[2] for r in rows]))) if rows else 0.0
            fit = self._prior_fit(tenant_id, sku_id, len(rows), sku["base_price"], mean_log_q)

        p_star, regime = pm.unconstrained_optimum(
            sku["unit_cogs"],
            fit["elasticity_epsilon"] if fit["elasticity_epsilon"] < 0 else settings.PRIOR_ELASTICITY,
            sku["max_msrp"],
        )
        if fit["elasticity_epsilon"] >= 0:
            regime = pm.REGIME_PRIOR
        fit.update({
            "sku_id": sku_id,
            "tenant_id": tenant_id,
            "optimization_regime": regime,
            "raw_optimal_price": p_star,
            "theoretical_lerner_index": pm.lerner_index(p_star, sku["unit_cogs"]),
        })
        return fit

    def _get_model_params(self, tenant_id: str, sku_id: str) -> Dict[str, float]:
        with lakehouse.get_connection(read_only=True) as conn:
            gold = conn.execute("""
                SELECT elasticity_epsilon, r_squared, cross_elasticity_beta, alpha_intercept, smearing_factor
                FROM gold_sku_elasticity
                WHERE tenant_id = ? AND sku_id = ?
            """, [tenant_id, sku_id]).fetchone()
        if gold and gold[3] is not None:
            return {
                "epsilon": gold[0], "r_squared": gold[1], "beta": gold[2] or 0.0,
                "alpha": gold[3], "smearing": gold[4] or 1.0,
            }
        fit = self.fit_sku_elasticity(tenant_id, sku_id)
        return {
            "epsilon": fit["elasticity_epsilon"], "r_squared": fit["r_squared"],
            "beta": fit["cross_elasticity_beta"], "alpha": fit["alpha"], "smearing": fit["smearing_factor"],
        }

    def compute_guarded_quote(
        self,
        tenant_id: str,
        sku_id: str,
        inventory_level: int,
        competitor_price: Optional[float] = None,
        demand_surge_factor: float = 1.0,
    ) -> Dict[str, Any]:
        """Compute a real-time optimal price with hard guardrails.

        1. P* = c * eps / (1 + eps)           (Lerner / inverse-elasticity rule)
        2. P_anchor = P*^(1-w) * P_comp^w     (competitor reference anchoring)
        3. P_inv: sell-through band on days of cover (scarcity premium / overstock markdown)
        4. P_rec = min(MSRP, max(c * (1 + m), P_inv))   (non-bypassable circuit breaker)
        """
        sku = self._get_sku(tenant_id, sku_id)
        params = self._get_model_params(tenant_id, sku_id)

        result = pm.guarded_quote(
            unit_cogs=sku["unit_cogs"],
            min_margin_pct=sku["min_margin_pct"],
            max_msrp=sku["max_msrp"],
            base_price=sku["base_price"],
            epsilon=params["epsilon"],
            beta=params["beta"],
            alpha=params["alpha"],
            smearing=params["smearing"],
            inventory_level=inventory_level,
            competitor_price=competitor_price,
            demand_surge_factor=demand_surge_factor,
            anchor_weight=settings.COMPETITOR_ANCHOR_WEIGHT,
            min_days=settings.SELL_THROUGH_MIN_DAYS,
            max_days=settings.SELL_THROUGH_MAX_DAYS,
            prior_epsilon=settings.PRIOR_ELASTICITY,
        )

        # Final non-bypassable clamp (defense in depth on top of pricing_math)
        floor = result["margin_floor_price"]
        rec = max(floor, min(sku["max_msrp"], result["recommended_price"]))

        def r2(v: Optional[float]) -> Optional[float]:
            return None if v is None else round(float(v), 2)

        def r4(v: Optional[float]) -> Optional[float]:
            return None if v is None else round(float(v), 4)

        waterfall = [
            {"stage": "Unit COGS", "price": r2(sku["unit_cogs"]), "formula": "c"},
            {"stage": "Margin floor", "price": r2(floor), "formula": "c(1+m)"},
            {"stage": "Lerner optimum P*", "price": r2(result["raw_optimal_price"]), "formula": "c·ε/(1+ε)"},
            {"stage": "Competitor anchor", "price": r2(result["anchored_price"]), "formula": "P*^(1-w)·Pc^w"},
            {"stage": "Inventory sell-through", "price": r2(result["inventory_adjusted_price"]), "formula": "P·(I/(Q·T))^(1/ε)"},
            {"stage": "Guarded recommendation", "price": r2(rec), "formula": "min(MSRP, max(floor, P))"},
        ]

        return {
            "sku_id": sku_id,
            "tenant_id": tenant_id,
            "recommended_price": r2(rec),
            "raw_optimal_price": r2(result["raw_optimal_price"]),
            "anchored_price": r2(result["anchored_price"]),
            "inventory_adjusted_price": r2(result["inventory_adjusted_price"]),
            "base_price": r2(sku["base_price"]),
            "unit_cogs": r2(sku["unit_cogs"]),
            "min_margin_pct": r4(sku["min_margin_pct"]),
            "margin_floor_price": r2(floor),
            "floor_gross_margin_pct": r4(result["floor_gross_margin_pct"]),
            "max_msrp_ceiling": r2(sku["max_msrp"]),
            "elasticity_epsilon": r4(params["epsilon"]),
            "cross_elasticity_beta": r4(params["beta"]),
            "r_squared": r4(params["r_squared"]),
            "optimization_regime": result["optimization_regime"],
            "inventory_regime": result["inventory_regime"],
            "guardrail_status": result["guardrail_status"],
            "lerner_index": r4(result["lerner_index"]),
            "theoretical_lerner_index": r4(result["theoretical_lerner_index"]),
            "markup_on_cogs_pct": r4(result["markup_on_cogs_pct"]),
            "expected_margin_usd": r2(result["expected_margin_usd"]),
            "expected_margin_pct": r4(result["expected_margin_pct"]),
            "expected_daily_units": r2(result["expected_daily_units"]),
            "expected_daily_profit_usd": r2(result["expected_daily_profit_usd"]),
            "days_of_cover": r2(result["days_of_cover"]),
            "expected_profit_uplift_pct": r4(result["expected_profit_uplift_pct"]),
            "breakeven_volume_change_pct": r4(result["breakeven_volume_change_pct"]),
            "inventory_level": inventory_level,
            "competitor_price": competitor_price,
            "demand_surge_factor": demand_surge_factor,
            "pricing_waterfall": waterfall,
            "calculated_at": datetime.now(timezone.utc).isoformat(),
        }

    def train_and_persist_gold_elasticity(self, tenant_id: str) -> int:
        """Fit elasticity models for all tenant SKUs and save to gold_sku_elasticity."""
        skus = lakehouse.get_sku_catalog(tenant_id)
        rows = []
        for s in skus:
            fit = self.fit_sku_elasticity(tenant_id, s["sku_id"])
            eps = fit["elasticity_epsilon"] if fit["elasticity_epsilon"] < 0 else settings.PRIOR_ELASTICITY
            # Neutral inventory: the middle of the sell-through band at the optimum.
            p_star = fit["raw_optimal_price"]
            q_star = pm.expected_demand(fit["alpha"], eps, fit["cross_elasticity_beta"], p_star,
                                        s["base_price"], 1.0, fit["smearing_factor"])
            neutral_inventory = q_star * (settings.SELL_THROUGH_MIN_DAYS + settings.SELL_THROUGH_MAX_DAYS) / 2.0
            quote = pm.guarded_quote(
                unit_cogs=s["unit_cogs"], min_margin_pct=s["min_margin_pct"], max_msrp=s["max_msrp"],
                base_price=s["base_price"], epsilon=fit["elasticity_epsilon"],
                beta=fit["cross_elasticity_beta"], alpha=fit["alpha"], smearing=fit["smearing_factor"],
                inventory_level=neutral_inventory, competitor_price=None, demand_surge_factor=1.0,
                anchor_weight=settings.COMPETITOR_ANCHOR_WEIGHT, min_days=settings.SELL_THROUGH_MIN_DAYS,
                max_days=settings.SELL_THROUGH_MAX_DAYS, prior_epsilon=settings.PRIOR_ELASTICITY,
            )
            rows.append([
                s["sku_id"], tenant_id,
                round(fit["elasticity_epsilon"], 6), round(fit["r_squared"], 6),
                round(float(quote["recommended_price"]), 2),
                # Expected gross-profit uplift vs. the static base price (not a price change).
                round(float(quote["expected_profit_uplift_pct"]) * 100.0, 4),
                fit["cross_elasticity_beta"], fit["alpha"], fit["smearing_factor"],
                fit["std_error_epsilon"], fit["adj_r_squared"], fit["sample_size"],
                float(quote["raw_optimal_price"]),
            ])

        with lakehouse.get_connection(read_only=False) as conn:
            for r in rows:
                conn.execute("""
                    INSERT OR REPLACE INTO gold_sku_elasticity (
                        sku_id, tenant_id, elasticity_epsilon, r_squared, recommended_price,
                        expected_margin_uplift_pct, updated_at, cross_elasticity_beta, alpha_intercept,
                        smearing_factor, elasticity_std_error, adj_r_squared, sample_size, raw_optimal_price
                    ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?)
                """, r)
        return len(rows)


elasticity_service = ElasticityModelService()
