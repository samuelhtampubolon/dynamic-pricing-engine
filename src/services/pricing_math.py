"""Pure, side-effect-free quantitative pricing formulas.

Every function here is deterministic and free of I/O so it can be unit tested
in isolation and mirrored exactly by the client-side engine in
``docs/assets/js/engine.js`` (a parity test keeps the two in sync).

Demand model (constant elasticity, estimated by log-log OLS):

    Q(P) = S * s * exp(alpha) * P**epsilon * P_comp**beta

where ``S`` is Duan's smearing factor and ``s`` the demand surge factor.
"""

from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

OPTIMAL_UNCONSTRAINED = "OPTIMAL_UNCONSTRAINED"
CLAMPED_TO_MARGIN_FLOOR = "CLAMPED_TO_MARGIN_FLOOR"
CLAMPED_TO_MSRP_CEILING = "CLAMPED_TO_MSRP_CEILING"

REGIME_LERNER = "LERNER_INTERIOR_OPTIMUM"
REGIME_INELASTIC = "INELASTIC_CORNER_SOLUTION"
REGIME_PRIOR = "PRIOR_ELASTICITY_FALLBACK"

INVENTORY_BALANCED = "SELL_THROUGH_BALANCED"
INVENTORY_SCARCITY = "SCARCITY_PREMIUM"
INVENTORY_OVERSTOCK = "OVERSTOCK_MARKDOWN"
INVENTORY_STOCKOUT = "STOCKOUT"

_ELASTIC_EPS = 1e-6


class GuardrailConfigError(ValueError):
    """Raised when the margin floor exceeds the MSRP ceiling (no feasible price)."""


# ---------------------------------------------------------------------------
# Margin / markup algebra
# ---------------------------------------------------------------------------
def margin_floor_price(unit_cogs: float, min_margin_pct: float) -> float:
    """P_floor = c * (1 + m). ``m`` is a markup on COGS (spec FR-03)."""
    return unit_cogs * (1.0 + min_margin_pct)


def markup_to_gross_margin(markup: float) -> float:
    """Gross margin on price implied by a markup on cost: g = m / (1 + m)."""
    return markup / (1.0 + markup)


def gross_margin_to_markup(gross_margin: float) -> float:
    """Markup on cost implied by a gross margin on price: m = g / (1 - g)."""
    if gross_margin >= 1.0:
        raise ValueError("Gross margin must be < 100%.")
    return gross_margin / (1.0 - gross_margin)


def lerner_index(price: float, marginal_cost: float) -> float:
    """L = (P - MC) / P."""
    return (price - marginal_cost) / price if price > 0 else 0.0


# ---------------------------------------------------------------------------
# Optimal price
# ---------------------------------------------------------------------------
def lerner_optimal_price(unit_cogs: float, epsilon: float) -> float:
    """Profit-maximising price under constant elasticity (epsilon < -1).

    From d/dP [(P - c) * A * P**eps] = 0  =>  P* = c * eps / (1 + eps),
    equivalently the inverse-elasticity rule (P* - c) / P* = -1 / eps.
    """
    if epsilon >= -1.0 - _ELASTIC_EPS:
        raise ValueError("Interior optimum requires elastic demand (epsilon < -1).")
    return unit_cogs * (epsilon / (1.0 + epsilon))


def unconstrained_optimum(unit_cogs: float, epsilon: float, max_msrp: float) -> Tuple[float, str]:
    """Return (P*, regime).

    For -1 <= epsilon < 0 profit is strictly increasing in price, so there is no
    interior optimum: the solution is the corner at the MSRP ceiling.
    """
    if epsilon < -1.0 - _ELASTIC_EPS:
        return lerner_optimal_price(unit_cogs, epsilon), REGIME_LERNER
    return max_msrp, REGIME_INELASTIC


# ---------------------------------------------------------------------------
# Demand
# ---------------------------------------------------------------------------
def expected_demand(
    alpha: float,
    epsilon: float,
    beta: float,
    price: float,
    competitor_price: float,
    surge: float = 1.0,
    smearing: float = 1.0,
) -> float:
    """E[Q] = S * s * exp(alpha + eps * ln P + beta * ln P_comp)."""
    return smearing * surge * math.exp(alpha + epsilon * math.log(price) + beta * math.log(competitor_price))


def competitor_anchor(price: float, competitor_price: Optional[float], weight: float) -> float:
    """Geometric reference-price anchoring: P_anchor = P^(1-w) * P_comp^w."""
    if competitor_price is None or competitor_price <= 0 or weight <= 0:
        return price
    return math.exp((1.0 - weight) * math.log(price) + weight * math.log(competitor_price))


def sell_through_price(ref_price: float, ref_daily_units: float, inventory: float, target_days: float, epsilon: float) -> float:
    """Price at which expected demand clears ``inventory`` in ``target_days``.

    Solve Q(P) * T = I with Q(P) = Q_ref * (P / P_ref)**eps:
        P = P_ref * (I / (Q_ref * T)) ** (1 / eps)
    """
    return ref_price * (inventory / (ref_daily_units * target_days)) ** (1.0 / epsilon)


def inventory_adjusted_price(
    price: float,
    daily_units: float,
    inventory: float,
    epsilon: float,
    min_days: float,
    max_days: float,
    ceiling: float,
) -> Tuple[float, str, Optional[float]]:
    """Sell-through band rule. Returns (price, regime, days_of_cover_at_input_price).

    * DoC < T_min  -> raise price until DoC = T_min (scarcity premium)
    * DoC > T_max  -> lower price until DoC = T_max (overstock markdown)
    * otherwise    -> keep price
    """
    if inventory <= 0:
        return ceiling, INVENTORY_STOCKOUT, 0.0
    if daily_units <= 0:
        return price, INVENTORY_BALANCED, None
    days_of_cover = inventory / daily_units
    if days_of_cover < min_days:
        return sell_through_price(price, daily_units, inventory, min_days, epsilon), INVENTORY_SCARCITY, days_of_cover
    if days_of_cover > max_days:
        return sell_through_price(price, daily_units, inventory, max_days, epsilon), INVENTORY_OVERSTOCK, days_of_cover
    return price, INVENTORY_BALANCED, days_of_cover


# ---------------------------------------------------------------------------
# Guardrail circuit breaker
# ---------------------------------------------------------------------------
def clamp_to_guardrails(price: float, floor: float, ceiling: float) -> Tuple[float, str]:
    """P_rec = min(ceiling, max(floor, P)). Non-bypassable; NaN fails closed to the floor."""
    if floor > ceiling:
        raise GuardrailConfigError(f"Margin floor {floor:.2f} exceeds MSRP ceiling {ceiling:.2f}.")
    if math.isnan(price):
        return floor, CLAMPED_TO_MARGIN_FLOOR
    if price < floor:
        status = CLAMPED_TO_MARGIN_FLOOR
    elif price > ceiling:
        status = CLAMPED_TO_MSRP_CEILING
    else:
        status = OPTIMAL_UNCONSTRAINED
    final = max(floor, min(ceiling, price))
    assert floor <= final <= ceiling
    return final, status


# ---------------------------------------------------------------------------
# Price-change economics
# ---------------------------------------------------------------------------
def profit_uplift_pct(new_price: float, base_price: float, unit_cogs: float, epsilon: float) -> float:
    """Expected gross-profit change of moving from P_0 to P_1 (demand shifters cancel):

        Delta Pi % = ((P1 - c) / (P0 - c)) * (P1 / P0)**eps - 1
    """
    base_unit_margin = base_price - unit_cogs
    if base_unit_margin <= 0:
        raise ValueError("Base price must exceed unit COGS.")
    return ((new_price - unit_cogs) / base_unit_margin) * (new_price / base_price) ** epsilon - 1.0


def breakeven_volume_change(price_change_pct: float, contribution_margin_pct: float) -> Optional[float]:
    """Volume change needed to keep gross profit constant after a price change.

        Delta Q % = -Delta P % / (CM % + Delta P %)
    """
    denom = contribution_margin_pct + price_change_pct
    if denom <= 0:
        return None
    return -price_change_pct / denom


def arc_elasticity(q1: float, q2: float, p1: float, p2: float) -> float:
    """Midpoint (arc) elasticity: [(Q2-Q1)/((Q1+Q2)/2)] / [(P2-P1)/((P1+P2)/2)]."""
    dp = (p2 - p1) / ((p1 + p2) / 2.0)
    if dp == 0:
        raise ValueError("Prices must differ.")
    return ((q2 - q1) / ((q1 + q2) / 2.0)) / dp


def guarded_quote(
    *,
    unit_cogs: float,
    min_margin_pct: float,
    max_msrp: float,
    base_price: float,
    epsilon: float,
    beta: float,
    alpha: float,
    smearing: float,
    inventory_level: float,
    competitor_price: Optional[float],
    demand_surge_factor: float,
    anchor_weight: float,
    min_days: float,
    max_days: float,
    prior_epsilon: float = -2.0,
) -> Dict[str, object]:
    """Full deterministic pricing pipeline (no I/O).

    1. Unconstrained optimum (Lerner / corner)      -> P*
    2. Competitor reference anchoring                -> P_anchor
    3. Inventory sell-through band                   -> P_inv
    4. Margin floor / MSRP ceiling circuit breaker   -> P_rec
    """
    floor = margin_floor_price(unit_cogs, min_margin_pct)
    if floor > max_msrp:
        raise GuardrailConfigError(f"Margin floor {floor:.2f} exceeds MSRP ceiling {max_msrp:.2f}.")

    regime_override = None
    pricing_eps = epsilon
    if epsilon >= 0:  # wrong-signed estimate: fall back to a conservative prior
        pricing_eps = prior_epsilon
        regime_override = REGIME_PRIOR

    p_star, regime = unconstrained_optimum(unit_cogs, pricing_eps, max_msrp)
    regime = regime_override or regime

    p_anchor = competitor_anchor(p_star, competitor_price, anchor_weight)
    comp_ref = competitor_price if competitor_price else base_price

    q_anchor = expected_demand(alpha, pricing_eps, beta, p_anchor, comp_ref, demand_surge_factor, smearing)
    p_inv, inv_regime, _ = inventory_adjusted_price(
        p_anchor, q_anchor, inventory_level, pricing_eps, min_days, max_days, max_msrp
    )

    p_rec, status = clamp_to_guardrails(p_inv, floor, max_msrp)

    q_rec = expected_demand(alpha, pricing_eps, beta, p_rec, comp_ref, demand_surge_factor, smearing)
    unit_margin = p_rec - unit_cogs
    price_change = p_rec / base_price - 1.0
    base_cm = (base_price - unit_cogs) / base_price

    return {
        "recommended_price": p_rec,
        "raw_optimal_price": p_star,
        "anchored_price": p_anchor,
        "inventory_adjusted_price": p_inv,
        "margin_floor_price": floor,
        "floor_gross_margin_pct": markup_to_gross_margin(min_margin_pct),
        "max_msrp_ceiling": max_msrp,
        "guardrail_status": status,
        "optimization_regime": regime,
        "inventory_regime": inv_regime,
        "pricing_elasticity": pricing_eps,
        "lerner_index": lerner_index(p_rec, unit_cogs),
        "theoretical_lerner_index": (-1.0 / pricing_eps) if regime == REGIME_LERNER else lerner_index(p_star, unit_cogs),
        "markup_on_cogs_pct": unit_margin / unit_cogs,
        "expected_margin_usd": unit_margin,
        "expected_margin_pct": unit_margin / p_rec,
        "expected_daily_units": q_rec,
        "expected_daily_profit_usd": unit_margin * q_rec,
        "days_of_cover": (inventory_level / q_rec) if q_rec > 0 else None,
        "expected_profit_uplift_pct": profit_uplift_pct(p_rec, base_price, unit_cogs, pricing_eps),
        "breakeven_volume_change_pct": breakeven_volume_change(price_change, base_cm),
    }
