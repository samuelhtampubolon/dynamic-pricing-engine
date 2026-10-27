/*
 * Dynamic Pricing & Unit Economics Engine: client-side quantitative core.
 *
 * Pure functions only (no DOM). This file mirrors src/services/pricing_math.py,
 * src/services/elasticity_model.py (OLS), src/services/bandit_simulator.py and
 * src/services/unit_economics.py; tests/test_js_engine_parity.py runs it under
 * Node and checks it against the Python implementation.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.PricingEngine = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const STATUS = {
    OPTIMAL: "OPTIMAL_UNCONSTRAINED",
    FLOOR: "CLAMPED_TO_MARGIN_FLOOR",
    CEILING: "CLAMPED_TO_MSRP_CEILING",
  };
  const REGIME = {
    LERNER: "LERNER_INTERIOR_OPTIMUM",
    INELASTIC: "INELASTIC_CORNER_SOLUTION",
    PRIOR: "PRIOR_ELASTICITY_FALLBACK",
  };
  const INVENTORY = {
    BALANCED: "SELL_THROUGH_BALANCED",
    SCARCITY: "SCARCITY_PREMIUM",
    OVERSTOCK: "OVERSTOCK_MARKDOWN",
    STOCKOUT: "STOCKOUT",
  };
  const POLICY = { anchorWeight: 0.3, minDays: 7, maxDays: 45, priorEpsilon: -2.0, annualDiscountRate: 0.1, zTolerance: 5.0, maxQuarantineRatio: 0.2 };
  const ELASTIC_EPS = 1e-6;

  // Catalog mirrors src/seed_market_data.py
  const CATALOG = [
    { sku_id: "SKU-GPU-H100", tenant_id: "tenant_alpha", sku_name: "Tensor GPU Blade 80GB", unit_cogs: 21500, min_margin_pct: 0.22, max_msrp: 39500, base_price: 31000, true_elasticity: -2.6, base_daily_demand: 24 },
    { sku_id: "SKU-NET-SWITCH-400G", tenant_id: "tenant_alpha", sku_name: "400GbE Spine Switch", unit_cogs: 4800, min_margin_pct: 0.25, max_msrp: 9200, base_price: 7500, true_elasticity: -2.4, base_daily_demand: 50 },
    { sku_id: "SKU-NVME-STORAGE-30TB", tenant_id: "tenant_alpha", sku_name: "Gen5 NVMe 30.72TB", unit_cogs: 1850, min_margin_pct: 0.2, max_msrp: 3800, base_price: 2900, true_elasticity: -2.9, base_daily_demand: 120 },
    { sku_id: "SKU-CNC-SPINDLE", tenant_id: "tenant_beta", sku_name: "5-Axis CNC Spindle", unit_cogs: 3400, min_margin_pct: 0.25, max_msrp: 6900, base_price: 5400, true_elasticity: -2.5, base_daily_demand: 35 },
    { sku_id: "SKU-COLD-REEFER", tenant_id: "tenant_beta", sku_name: "IoT Cold-Chain Controller", unit_cogs: 420, min_margin_pct: 0.3, max_msrp: 1150, base_price: 820, true_elasticity: -3.2, base_daily_demand: 210 },
  ];
  const TRUE_CROSS_ELASTICITY = 0.35;

  // ------------------------------------------------------------------ RNG
  function mulberry32(seed) {
    let a = seed >>> 0;
    return function () {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function makeRng(seed) {
    const u = mulberry32(seed);
    let spare = null;
    const rng = {
      random: u,
      uniform: (lo, hi) => lo + (hi - lo) * u(),
      normal(mu, sigma) {
        if (spare !== null) { const z = spare; spare = null; return mu + sigma * z; }
        let x, y, s;
        do { x = 2 * u() - 1; y = 2 * u() - 1; s = x * x + y * y; } while (s >= 1 || s === 0);
        const m = Math.sqrt((-2 * Math.log(s)) / s);
        spare = y * m;
        return mu + sigma * x * m;
      },
      poisson(lambda) {
        if (lambda <= 0) return 0;
        if (lambda < 40) { // Knuth
          const L = Math.exp(-lambda); let k = 0, p = 1;
          do { k++; p *= u(); } while (p > L);
          return k - 1;
        }
        return Math.max(0, Math.round(rng.normal(lambda, Math.sqrt(lambda)))); // normal approximation
      },
      integer: (n) => Math.floor(u() * n),
    };
    return rng;
  }

  // ------------------------------------------------------- margin algebra
  const marginFloorPrice = (c, m) => c * (1 + m);
  const markupToGrossMargin = (m) => m / (1 + m);
  const grossMarginToMarkup = (g) => g / (1 - g);
  const lernerIndex = (p, mc) => (p > 0 ? (p - mc) / p : 0);

  function lernerOptimalPrice(c, eps) {
    if (eps >= -1 - ELASTIC_EPS) throw new Error("Interior optimum requires epsilon < -1");
    return c * (eps / (1 + eps));
  }
  function unconstrainedOptimum(c, eps, msrp) {
    if (eps < -1 - ELASTIC_EPS) return [lernerOptimalPrice(c, eps), REGIME.LERNER];
    return [msrp, REGIME.INELASTIC];
  }

  // --------------------------------------------------------------- demand
  const expectedDemand = (alpha, eps, beta, p, pc, surge = 1, smear = 1) =>
    smear * surge * Math.exp(alpha + eps * Math.log(p) + beta * Math.log(pc));

  function competitorAnchor(p, pc, w) {
    if (pc == null || pc <= 0 || w <= 0) return p;
    return Math.exp((1 - w) * Math.log(p) + w * Math.log(pc));
  }
  const sellThroughPrice = (pRef, qRef, inv, days, eps) => pRef * Math.pow(inv / (qRef * days), 1 / eps);

  function inventoryAdjustedPrice(p, q, inv, eps, minDays, maxDays, ceiling) {
    if (inv <= 0) return [ceiling, INVENTORY.STOCKOUT, 0];
    if (q <= 0) return [p, INVENTORY.BALANCED, null];
    const doc = inv / q;
    if (doc < minDays) return [sellThroughPrice(p, q, inv, minDays, eps), INVENTORY.SCARCITY, doc];
    if (doc > maxDays) return [sellThroughPrice(p, q, inv, maxDays, eps), INVENTORY.OVERSTOCK, doc];
    return [p, INVENTORY.BALANCED, doc];
  }

  function clampToGuardrails(p, floor, ceiling) {
    if (floor > ceiling) throw new Error("Margin floor exceeds MSRP ceiling");
    if (Number.isNaN(p)) return [floor, STATUS.FLOOR];
    const status = p < floor ? STATUS.FLOOR : p > ceiling ? STATUS.CEILING : STATUS.OPTIMAL;
    return [Math.max(floor, Math.min(ceiling, p)), status];
  }

  function profitUpliftPct(p1, p0, c, eps) {
    if (p0 - c <= 0) throw new Error("Base price must exceed unit COGS");
    return ((p1 - c) / (p0 - c)) * Math.pow(p1 / p0, eps) - 1;
  }
  function breakevenVolumeChange(dp, cm) {
    const d = cm + dp;
    return d <= 0 ? null : -dp / d;
  }
  function arcElasticity(q1, q2, p1, p2) {
    return ((q2 - q1) / ((q1 + q2) / 2)) / ((p2 - p1) / ((p1 + p2) / 2));
  }

  function guardedQuote(o) {
    const floor = marginFloorPrice(o.unit_cogs, o.min_margin_pct);
    if (floor > o.max_msrp) throw new Error("Margin floor exceeds MSRP ceiling");
    let eps = o.epsilon, override = null;
    const prior = o.prior_epsilon ?? POLICY.priorEpsilon;
    if (eps >= 0) { eps = prior; override = REGIME.PRIOR; }
    let [pStar, regime] = unconstrainedOptimum(o.unit_cogs, eps, o.max_msrp);
    regime = override || regime;
    const pAnchor = competitorAnchor(pStar, o.competitor_price, o.anchor_weight);
    const compRef = o.competitor_price ? o.competitor_price : o.base_price;
    const qAnchor = expectedDemand(o.alpha, eps, o.beta, pAnchor, compRef, o.demand_surge_factor, o.smearing);
    const [pInv, invRegime] = inventoryAdjustedPrice(pAnchor, qAnchor, o.inventory_level, eps, o.min_days, o.max_days, o.max_msrp);
    const [pRec, status] = clampToGuardrails(pInv, floor, o.max_msrp);
    const qRec = expectedDemand(o.alpha, eps, o.beta, pRec, compRef, o.demand_surge_factor, o.smearing);
    const unitMargin = pRec - o.unit_cogs;
    return {
      recommended_price: pRec,
      raw_optimal_price: pStar,
      anchored_price: pAnchor,
      inventory_adjusted_price: pInv,
      margin_floor_price: floor,
      floor_gross_margin_pct: markupToGrossMargin(o.min_margin_pct),
      max_msrp_ceiling: o.max_msrp,
      guardrail_status: status,
      optimization_regime: regime,
      inventory_regime: invRegime,
      pricing_elasticity: eps,
      lerner_index: lernerIndex(pRec, o.unit_cogs),
      theoretical_lerner_index: regime === REGIME.LERNER ? -1 / eps : lernerIndex(pStar, o.unit_cogs),
      markup_on_cogs_pct: unitMargin / o.unit_cogs,
      expected_margin_usd: unitMargin,
      expected_margin_pct: unitMargin / pRec,
      expected_daily_units: qRec,
      expected_daily_profit_usd: unitMargin * qRec,
      days_of_cover: qRec > 0 ? o.inventory_level / qRec : null,
      expected_profit_uplift_pct: profitUpliftPct(pRec, o.base_price, o.unit_cogs, eps),
      breakeven_volume_change_pct: breakevenVolumeChange(pRec / o.base_price - 1, (o.base_price - o.unit_cogs) / o.base_price),
    };
  }

  // ------------------------------------------------------ linear algebra
  function invert(M) {
    const n = M.length;
    const A = M.map((row, i) => [...row, ...Array.from({ length: n }, (_, j) => (i === j ? 1 : 0))]);
    for (let c = 0; c < n; c++) {
      let piv = c;
      for (let r = c + 1; r < n; r++) if (Math.abs(A[r][c]) > Math.abs(A[piv][c])) piv = r;
      if (Math.abs(A[piv][c]) < 1e-14) throw new Error("Singular matrix");
      [A[c], A[piv]] = [A[piv], A[c]];
      const d = A[c][c];
      for (let j = 0; j < 2 * n; j++) A[c][j] /= d;
      for (let r = 0; r < n; r++) {
        if (r === c) continue;
        const f = A[r][c];
        if (f !== 0) for (let j = 0; j < 2 * n; j++) A[r][j] -= f * A[c][j];
      }
    }
    return A.map((row) => row.slice(n));
  }
  function olsSolve(X, y) {
    const k = X[0].length, n = X.length;
    const XtX = Array.from({ length: k }, () => new Array(k).fill(0));
    const Xty = new Array(k).fill(0);
    for (let i = 0; i < n; i++) {
      const xi = X[i];
      for (let a = 0; a < k; a++) {
        Xty[a] += xi[a] * y[i];
        for (let b = a; b < k; b++) XtX[a][b] += xi[a] * xi[b];
      }
    }
    for (let a = 0; a < k; a++) for (let b = 0; b < a; b++) XtX[a][b] = XtX[b][a];
    const inv = invert(XtX);
    const beta = inv.map((row) => row.reduce((s, v, j) => s + v * Xty[j], 0));
    return { beta, inv };
  }

  // ---------------------------------------------- Student-t distribution
  function logGamma(x) {
    const g = [76.18009172947146, -86.50532032941677, 24.01409824083091, -1.231739572450155, 0.1208650973866179e-2, -0.5395239384953e-5];
    let y = x, tmp = x + 5.5;
    tmp -= (x + 0.5) * Math.log(tmp);
    let ser = 1.000000000190015;
    for (const c of g) ser += c / ++y;
    return -tmp + Math.log((2.5066282746310005 * ser) / x);
  }
  function betacf(a, b, x) {
    const MAXIT = 300, EPS = 3e-14, FPMIN = 1e-300;
    let qab = a + b, qap = a + 1, qam = a - 1, c = 1, d = 1 - (qab * x) / qap;
    if (Math.abs(d) < FPMIN) d = FPMIN;
    d = 1 / d;
    let h = d;
    for (let m = 1; m <= MAXIT; m++) {
      const m2 = 2 * m;
      let aa = (m * (b - m) * x) / ((qam + m2) * (a + m2));
      d = 1 + aa * d; if (Math.abs(d) < FPMIN) d = FPMIN;
      c = 1 + aa / c; if (Math.abs(c) < FPMIN) c = FPMIN;
      d = 1 / d; h *= d * c;
      aa = (-(a + m) * (qab + m) * x) / ((a + m2) * (qap + m2));
      d = 1 + aa * d; if (Math.abs(d) < FPMIN) d = FPMIN;
      c = 1 + aa / c; if (Math.abs(c) < FPMIN) c = FPMIN;
      d = 1 / d;
      const del = d * c; h *= del;
      if (Math.abs(del - 1) < EPS) break;
    }
    return h;
  }
  function incompleteBeta(a, b, x) {
    if (x <= 0) return 0;
    if (x >= 1) return 1;
    const bt = Math.exp(logGamma(a + b) - logGamma(a) - logGamma(b) + a * Math.log(x) + b * Math.log(1 - x));
    return x < (a + 1) / (a + b + 2) ? (bt * betacf(a, b, x)) / a : 1 - (bt * betacf(b, a, 1 - x)) / b;
  }
  /** Two-sided p-value P(|T| > |t|) for Student-t with df degrees of freedom. */
  const tTwoSidedP = (t, df) => incompleteBeta(df / 2, 0.5, df / (df + t * t));
  function tQuantile975(df) {
    let lo = 0, hi = 50;
    for (let i = 0; i < 100; i++) {
      const mid = (lo + hi) / 2;
      if (tTwoSidedP(mid, df) > 0.05) lo = mid; else hi = mid;
    }
    return (lo + hi) / 2;
  }

  // --------------------------------------------------------- log-log OLS
  function fitLogLogOls(prices, comps, qty, dow) {
    const rows = [];
    for (let i = 0; i < prices.length; i++) {
      if (prices[i] > 0 && comps[i] > 0 && qty[i] > 0) rows.push(i);
    }
    const n = rows.length;
    const dowCols = dow ? [1, 2, 3, 4, 5, 6].filter((d) => rows.some((i) => dow[i] === d)) : [];
    const X = rows.map((i) => [1, Math.log(prices[i]), Math.log(comps[i]), ...dowCols.map((d) => (dow[i] === d ? 1 : 0))]);
    const y = rows.map((i) => Math.log(qty[i]));
    const k = X[0].length;
    const dof = n - k;
    const { beta, inv } = olsSolve(X, y);
    const yhat = X.map((r) => r.reduce((s, v, j) => s + v * beta[j], 0));
    const resid = y.map((v, i) => v - yhat[i]);
    const ybar = y.reduce((s, v) => s + v, 0) / n;
    const rss = resid.reduce((s, e) => s + e * e, 0);
    const tss = y.reduce((s, v) => s + (v - ybar) ** 2, 0);
    const r2 = tss > 0 ? 1 - rss / tss : 0;
    const s2 = rss / dof;
    const se = inv.map((row, j) => Math.sqrt(Math.max(0, row[j] * s2)));
    const tEps = beta[1] / se[1];
    const tCrit = tQuantile975(dof);
    let dw = 0;
    for (let i = 1; i < n; i++) dw += (resid[i] - resid[i - 1]) ** 2;
    // VIF(ln P) from the auxiliary regression on the remaining regressors
    const others = X.map((r) => [r[0], ...r.slice(2)]);
    const lp = X.map((r) => r[1]);
    const aux = olsSolve(others, lp).beta;
    const lpBar = lp.reduce((s, v) => s + v, 0) / n;
    let auxRss = 0, auxTss = 0;
    others.forEach((r, i) => { const f = r.reduce((s, v, j) => s + v * aux[j], 0); auxRss += (lp[i] - f) ** 2; auxTss += (lp[i] - lpBar) ** 2; });
    const vif = 1 / Math.max(1e-12, 1 - (1 - auxRss / auxTss));
    const gammas = [0, ...[1, 2, 3, 4, 5, 6].map((d) => { const j = dowCols.indexOf(d); return j >= 0 ? beta[3 + j] : 0; })];
    const alphaAvg = beta[0] + Math.log(gammas.reduce((s, g) => s + Math.exp(g), 0) / gammas.length);
    return {
      elasticity_epsilon: beta[1],
      cross_elasticity_beta: beta[2],
      alpha: alphaAvg,
      alpha_monday: beta[0],
      dow_effects: gammas,
      r_squared: Math.max(0, Math.min(1, r2)),
      adj_r_squared: 1 - ((1 - r2) * (n - 1)) / dof,
      rmse_log: Math.sqrt(rss / n),
      std_error_epsilon: se[1],
      t_stat_epsilon: tEps,
      p_value_epsilon: tTwoSidedP(tEps, dof),
      ci95_epsilon_low: beta[1] - tCrit * se[1],
      ci95_epsilon_high: beta[1] + tCrit * se[1],
      std_error_beta: se[2],
      durbin_watson: rss > 0 ? dw / rss : 2,
      vif_log_price: vif,
      smearing_factor: resid.reduce((s, e) => s + Math.exp(e), 0) / n,
      aic: n * Math.log(Math.max(rss, 1e-12) / n) + 2 * k,
      bic: n * Math.log(Math.max(rss, 1e-12) / n) + k * Math.log(n),
      sample_size: n,
      residuals: resid,
      t_crit: tCrit,
    };
  }

  // ------------------------------------------------ synthetic market data
  /** Mirrors seed_market_data.py: daily events with DOW seasonality, cross-price effect and noise. */
  function generateMarketHistory(sku, days = 365, seed = 42) {
    const rng = makeRng(seed);
    const out = { price: [], comp: [], qty: [], dow: [], inventory: [] };
    for (let t = 0; t < days; t++) {
      const dow = t % 7;
      const dowFactor = 1 + 0.15 * Math.sin((dow * Math.PI) / 3);
      const comp = sku.base_price * rng.uniform(0.92, 1.08);
      const price = sku.base_price * rng.uniform(0.82, 1.18);
      const expQ = sku.base_daily_demand * Math.pow(price / sku.base_price, sku.true_elasticity) *
        Math.pow(comp / sku.base_price, TRUE_CROSS_ELASTICITY) * dowFactor;
      const q = Math.max(1, Math.round(rng.normal(expQ, Math.max(2, expQ * 0.15))));
      out.price.push(price); out.comp.push(comp); out.qty.push(q); out.dow.push(dow);
      out.inventory.push(Math.round(sku.base_daily_demand * rng.uniform(10, 40)));
    }
    return out;
  }

  // ------------------------------------------------------ robust z gate
  function median(xs) {
    const s = [...xs].sort((a, b) => a - b), m = s.length >> 1;
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  }
  const mad = (xs) => { const m = median(xs); return median(xs.map((x) => Math.abs(x - m))); };
  function stdPop(xs) { const m = xs.reduce((s, v) => s + v, 0) / xs.length; return Math.sqrt(xs.reduce((s, v) => s + (v - m) ** 2, 0) / xs.length); }
  function robustZ(xs) {
    const m = median(xs);
    const sigma = 1.4826 * mad(xs) || stdPop(xs) || 1;
    return xs.map((x) => (x - m) / sigma);
  }
  function classicZ(xs) {
    const m = xs.reduce((s, v) => s + v, 0) / xs.length;
    const s = stdPop(xs) || 1;
    return xs.map((x) => (x - m) / s);
  }

  // ------------------------------------------------------------- bandit
  function runBandit({ arms, expectedUnits, unitCogs, baselinePrice, baselineUnits, horizon, algorithm, epsilon = 0.1, seed = 7, historyPoints = 60 }) {
    const rng = makeRng(seed);
    const k = arms.length;
    const mu = arms.map((p, i) => (p - unitCogs) * expectedUnits[i]);
    const best = mu.indexOf(Math.max(...mu));
    const muStar = mu[best];
    const muStatic = (baselinePrice - unitCogs) * baselineUnits;
    const counts = new Array(k).fill(0), means = new Array(k).fill(0), m2 = new Array(k).fill(0);
    let cumProfit = 0, cumStatic = 0, cumRegret = 0, cumRev = 0, cumStaticRev = 0;
    const history = [];
    const every = Math.max(1, Math.floor(horizon / historyPoints));
    for (let t = 1; t <= horizon; t++) {
      let idx;
      if (t <= k) idx = t - 1;
      else {
        const nTot = counts.reduce((a, b) => a + b, 0);
        const pooled = m2.reduce((a, b) => a + b, 0) / Math.max(1, nTot - k);
        const s = Math.sqrt(Math.max(pooled, 1e-9));
        if (algorithm === "thompson_sampling") {
          let bestV = -Infinity;
          for (let i = 0; i < k; i++) { const v = rng.normal(means[i], s / Math.sqrt(counts[i])); if (v > bestV) { bestV = v; idx = i; } }
        } else if (algorithm === "ucb1") {
          let bestV = -Infinity;
          for (let i = 0; i < k; i++) { const v = means[i] + s * Math.sqrt((2 * Math.log(t)) / counts[i]); if (v > bestV) { bestV = v; idx = i; } }
        } else if (rng.random() < epsilon) idx = rng.integer(k);
        else idx = means.indexOf(Math.max(...means));
      }
      const q = rng.poisson(expectedUnits[idx]);
      const reward = (arms[idx] - unitCogs) * q;
      counts[idx]++;
      const delta = reward - means[idx];
      means[idx] += delta / counts[idx];
      m2[idx] += delta * (reward - means[idx]);
      const qs = rng.poisson(baselineUnits);
      cumProfit += reward; cumRev += arms[idx] * q;
      cumStatic += (baselinePrice - unitCogs) * qs; cumStaticRev += baselinePrice * qs;
      cumRegret += muStar - mu[idx];
      if (t % every === 0 || t === horizon) {
        history.push({ step: t, profit: cumProfit, staticProfit: cumStatic, regret: cumRegret, staticRegret: t * (muStar - muStatic) });
      }
    }
    const staticRegret = horizon * (muStar - muStatic);
    return {
      counts, means, mu, best, history,
      profit: cumProfit, staticProfit: cumStatic, revenue: cumRev, staticRevenue: cumStaticRev,
      regret: cumRegret, staticRegret,
      regretReductionPct: staticRegret > 0 ? (1 - cumRegret / staticRegret) * 100 : 0,
      profitUpliftPct: ((cumProfit - cumStatic) / Math.max(1, Math.abs(cumStatic))) * 100,
      optimalShare: counts[best] / horizon,
    };
  }

  // ------------------------------------------------------ unit economics
  const monthlyDiscountRate = (annual) => Math.pow(1 + annual, 1 / 12) - 1;
  const ltvSimple = (arpu, gm, churn) => (arpu * gm) / (churn <= 0.0001 ? 0.01 : churn);
  const ltvDiscounted = (arpu, gm, churn, d) => (churn + d > 0 ? (arpu * gm) / (churn + d) : 0);
  const paybackSimple = (cac, arpu, gm) => (arpu * gm > 0 ? cac / (arpu * gm) : 999);
  function paybackChurnAdjusted(cac, arpu, gm, churn) {
    const m = arpu * gm;
    if (m <= 0) return null;
    if (churn <= 0) return cac / m;
    const x = 1 - (cac * churn) / m;
    return x <= 0 ? null : Math.log(x) / Math.log(1 - churn);
  }
  const annualChurn = (c) => 1 - Math.pow(1 - c, 12);
  const halfLife = (c) => (c > 0 ? Math.log(0.5) / Math.log(1 - c) : Infinity);

  /** 12 monthly cohorts per tenant, same generator shape as seed_market_data.py. */
  function generateCohorts(tenant, seed = 42) {
    const rng = makeRng(seed + (tenant === "tenant_alpha" ? 1 : 2));
    const alpha = tenant === "tenant_alpha";
    const baseArpu = alpha ? 3800 : 1450, baseCac = alpha ? 4200 : 1850, baseN = alpha ? 140 : 380;
    return Array.from({ length: 12 }, (_, idx) => {
      const eff = 1 + idx * 0.02;
      return {
        cohort_id: `2025-${String(idx + 1).padStart(2, "0")}`,
        arpu: baseArpu * rng.uniform(0.95, 1.08),
        gm: rng.uniform(0.48, 0.58),
        churn: rng.uniform(0.025, 0.045) / eff,
        cac: (baseCac / eff) * rng.uniform(0.94, 1.05),
        n: Math.round(baseN * eff * rng.uniform(0.85, 1.15)),
      };
    });
  }

  return {
    STATUS, REGIME, INVENTORY, POLICY, CATALOG, TRUE_CROSS_ELASTICITY,
    makeRng, marginFloorPrice, markupToGrossMargin, grossMarginToMarkup, lernerIndex, lernerOptimalPrice,
    unconstrainedOptimum, expectedDemand, competitorAnchor, sellThroughPrice, inventoryAdjustedPrice,
    clampToGuardrails, profitUpliftPct, breakevenVolumeChange, arcElasticity, guardedQuote,
    fitLogLogOls, tTwoSidedP, tQuantile975, generateMarketHistory,
    median, mad, robustZ, classicZ, runBandit,
    monthlyDiscountRate, ltvSimple, ltvDiscounted, paybackSimple, paybackChurnAdjusted, annualChurn, halfLife, generateCohorts,
  };
});
