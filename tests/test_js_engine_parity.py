"""Parity between the GitHub Pages client engine (docs/assets/js/engine.js) and the Python engine.

The live demo must compute exactly what the API computes. Skipped when Node.js is unavailable.
"""

import json
import random
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.services import pricing_math as pm
from src.services.elasticity_model import fit_log_log_ols
from src.services.unit_economics import unit_economics_service as ue

ENGINE = Path(__file__).resolve().parent.parent / "docs" / "assets" / "js" / "engine.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js not installed")


def _run_node(script: str, payload: dict) -> dict:
    prog = f"const E = require({json.dumps(str(ENGINE))});\nconst input = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n{script}"
    out = subprocess.run([NODE, "-e", prog], input=json.dumps(payload), capture_output=True, text=True, timeout=60, check=True)
    return json.loads(out.stdout)


def test_guarded_quote_parity():
    rng = random.Random(5)
    cases = []
    for _ in range(300):
        c = rng.uniform(10, 20_000)
        m = rng.uniform(0.05, 0.6)
        msrp = c * (1 + m) * rng.uniform(1.05, 2.5)
        cases.append({
            "unit_cogs": c, "min_margin_pct": m, "max_msrp": msrp,
            "base_price": rng.uniform(c * (1 + m), msrp),
            "epsilon": rng.choice([rng.uniform(-6, -1.05), rng.uniform(-0.99, -0.1), 0.4]),
            "beta": rng.uniform(-0.5, 1.2), "alpha": rng.uniform(-5, 25), "smearing": rng.uniform(1, 1.05),
            "inventory_level": rng.choice([0, rng.uniform(1, 1e6)]),
            "competitor_price": rng.choice([None, rng.uniform(1, 1e6)]),
            "demand_surge_factor": rng.uniform(0.1, 10), "anchor_weight": 0.3, "min_days": 7.0, "max_days": 45.0,
            "prior_epsilon": -2.0,
        })
    js = _run_node("process.stdout.write(JSON.stringify(input.cases.map(E.guardedQuote)));", {"cases": cases})
    for case, got in zip(cases, js):
        want = pm.guarded_quote(**case)
        for key, value in want.items():
            if isinstance(value, str) or value is None:
                assert got[key] == value, key
            else:
                assert got[key] == pytest.approx(value, rel=1e-9, abs=1e-9), key


def test_ols_parity():
    gen = np.random.default_rng(3)
    n = 365
    p = 100 * gen.uniform(0.8, 1.2, n)
    pc = 100 * gen.uniform(0.9, 1.1, n)
    dow = (np.arange(n) % 7).tolist()
    q = np.maximum(1, np.round(np.exp(14 - 2.5 * np.log(p) + 0.3 * np.log(pc) + gen.normal(0, 0.15, n))))
    want = fit_log_log_ols(p, pc, q, dow)
    got = _run_node(
        "const r = E.fitLogLogOls(input.p, input.pc, input.q, input.dow); delete r.residuals; delete r.dow_effects;"
        "process.stdout.write(JSON.stringify(r));",
        {"p": p.tolist(), "pc": pc.tolist(), "q": q.tolist(), "dow": dow},
    )
    for key in ("elasticity_epsilon", "cross_elasticity_beta", "alpha", "r_squared", "adj_r_squared", "rmse_log",
                "std_error_epsilon", "t_stat_epsilon", "ci95_epsilon_low", "ci95_epsilon_high", "std_error_beta",
                "durbin_watson", "vif_log_price", "smearing_factor", "aic", "bic"):
        assert got[key] == pytest.approx(want[key], rel=1e-6, abs=1e-9), key
    assert got["sample_size"] == want["sample_size"]


def test_unit_economics_parity():
    got = _run_node(
        "const d = E.monthlyDiscountRate(0.1); process.stdout.write(JSON.stringify({"
        "d, ltv: E.ltvSimple(2000, 0.5, 0.05), dltv: E.ltvDiscounted(2000, 0.5, 0.05, d),"
        "pb: E.paybackSimple(4000, 2000, 0.5), pbc: E.paybackChurnAdjusted(4000, 2000, 0.5, 0.05),"
        "never: E.paybackChurnAdjusted(25000, 2000, 0.5, 0.05), ac: E.annualChurn(0.04), hl: E.halfLife(0.04)}));",
        {},
    )
    d = ue.monthly_discount_rate(0.1)
    assert got["d"] == pytest.approx(d)
    assert got["ltv"] == pytest.approx(ue.calculate_ltv(2000, 0.5, 0.05))
    assert got["dltv"] == pytest.approx(ue.calculate_discounted_ltv(2000, 0.5, 0.05, d), abs=0.01)
    assert got["pb"] == pytest.approx(ue.calculate_payback_months(4000, 2000, 0.5))
    assert got["pbc"] == pytest.approx(ue.calculate_churn_adjusted_payback(4000, 2000, 0.5, 0.05), abs=0.01)
    assert got["never"] is None
    assert got["ac"] == pytest.approx(ue.annual_churn_rate(0.04))
    assert got["hl"] == pytest.approx(ue.customer_half_life_months(0.04))
