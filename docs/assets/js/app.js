/* Dynamic Pricing Engine: GitHub Pages UI. All numbers come from engine.js. */
(function () {
  "use strict";

  const E = window.PricingEngine;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

  const C = {
    s1: cssVar("--series-1"), s2: cssVar("--series-2"), s3: cssVar("--series-3"), base: cssVar("--baseline"),
    text: cssVar("--text"), text2: cssVar("--text-2"), muted: cssVar("--muted"), grid: cssVar("--grid"),
    emerald: cssVar("--emerald"), critical: cssVar("--critical"), warning: cssVar("--warning"), good: cssVar("--good"),
    envelope: cssVar("--envelope"), surface: cssVar("--surface-solid"),
  };
  const POLICY = E.POLICY;

  // ------------------------------------------------------------ formatting
  const nf = (d) => new Intl.NumberFormat("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  function usd(v, d) {
    if (v == null || !Number.isFinite(v)) return "—";
    const digits = d ?? (Math.abs(v) >= 1000 ? 0 : 2);
    return (v < 0 ? "−$" : "$") + nf(digits).format(Math.abs(v));
  }
  function compactUsd(v) {
    const a = Math.abs(v), s = v < 0 ? "−$" : "$";
    if (a >= 1e9) return s + nf(2).format(a / 1e9) + "B";
    if (a >= 1e6) return s + nf(a >= 1e7 ? 0 : 1).format(a / 1e6) + "M";
    if (a >= 1e3) return s + nf(1).format(a / 1e3) + "k";
    return s + nf(0).format(a);
  }
  const pct = (v, d = 1) => (v == null || !Number.isFinite(v) ? "—" : (v * 100).toFixed(d) + "%");
  const signedPct = (v, d = 1) => (v == null || !Number.isFinite(v) ? "—" : (v >= 0 ? "+" : "−") + Math.abs(v * 100).toFixed(d) + "%");
  const num = (v, d = 2) => (v == null || !Number.isFinite(v) ? "—" : nf(d).format(v));
  const texNum = (v, d = 2) => nf(d).format(v).replace(/,/g, "{,}");
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function tex(el, src, display = true) {
    if (!window.katex) { el.textContent = src; return; }
    try { window.katex.render(src, el, { displayMode: display, throwOnError: false, output: "html" }); }
    catch (e) { el.textContent = src; }
  }
  function setFill(input) {
    const min = +input.min, max = +input.max, v = +input.value;
    input.style.setProperty("--fill", ((v - min) / (max - min)) * 100 + "%");
  }

  // ------------------------------------------------------------ charts
  function setupCharts() {
    const d = Chart.defaults;
    d.color = C.text2;
    d.font.family = cssVar("--font");
    d.font.size = 12;
    d.borderColor = C.grid;
    d.animation.duration = reduceMotion ? 0 : 450;
    d.plugins.legend.labels.usePointStyle = true;
    d.plugins.legend.labels.boxWidth = 8;
    d.plugins.legend.labels.boxHeight = 8;
    d.plugins.tooltip.backgroundColor = "rgba(6,10,18,0.94)";
    d.plugins.tooltip.borderColor = "rgba(148,163,184,0.26)";
    d.plugins.tooltip.borderWidth = 1;
    d.plugins.tooltip.padding = 10;
    d.plugins.tooltip.titleColor = C.text;
    d.plugins.tooltip.bodyColor = C.text;
    d.plugins.tooltip.cornerRadius = 10;
    d.elements.line.borderWidth = 2;
    d.elements.point.radius = 0;
    d.elements.point.hoverRadius = 5;
    d.maintainAspectRatio = false;
    d.responsive = true;
    Chart.register(crosshairPlugin, guidesPlugin);
  }

  const crosshairPlugin = {
    id: "crosshair",
    afterDatasetsDraw(chart) {
      const opts = chart.options.plugins.crosshair;
      if (!opts || !opts.enabled) return;
      const active = chart.tooltip && chart.tooltip.getActiveElements();
      if (!active || !active.length) return;
      const x = active[0].element.x, { top, bottom } = chart.chartArea, ctx = chart.ctx;
      ctx.save(); ctx.strokeStyle = "rgba(148,163,184,0.45)"; ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
      ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke(); ctx.restore();
    },
  };

  /** Shaded x-bands, vertical and horizontal reference lines with direct labels. */
  const guidesPlugin = {
    id: "guides",
    beforeDatasetsDraw(chart) {
      const g = chart.options.plugins.guides;
      if (!g || !g.bands) return;
      const { ctx, chartArea: a, scales: { x } } = chart;
      ctx.save();
      for (const b of g.bands) {
        const x0 = Math.max(a.left, x.getPixelForValue(b.from)), x1 = Math.min(a.right, x.getPixelForValue(b.to));
        if (x1 <= x0) continue;
        ctx.fillStyle = b.color; ctx.fillRect(x0, a.top, x1 - x0, a.bottom - a.top);
        if (b.label) { ctx.fillStyle = C.muted; ctx.font = "11px " + cssVar("--font"); ctx.fillText(b.label, x0 + 6, a.top + 14); }
      }
      ctx.restore();
    },
    afterDatasetsDraw(chart) {
      const g = chart.options.plugins.guides;
      if (!g) return;
      const { ctx, chartArea: a, scales: { x, y } } = chart;
      ctx.save();
      ctx.font = "600 11px " + cssVar("--font");
      for (const v of g.vlines || []) {
        const px = x.getPixelForValue(v.value);
        if (px < a.left - 1 || px > a.right + 1) continue;
        ctx.strokeStyle = v.color; ctx.lineWidth = v.width || 1.5; ctx.setLineDash(v.dash || [5, 4]);
        ctx.beginPath(); ctx.moveTo(px, a.top); ctx.lineTo(px, a.bottom); ctx.stroke();
        if (v.label) {
          ctx.setLineDash([]); ctx.fillStyle = C.text;
          const w = ctx.measureText(v.label).width, left = px + 5 + w > a.right ? px - 5 - w : px + 5;
          ctx.fillText(v.label, left, a.top + 14 + (v.offset || 0));
        }
      }
      for (const h of g.hlines || []) {
        const py = y.getPixelForValue(h.value);
        if (py < a.top || py > a.bottom) continue;
        ctx.strokeStyle = h.color; ctx.lineWidth = 1.5; ctx.setLineDash(h.dash || [5, 4]);
        ctx.beginPath(); ctx.moveTo(a.left, py); ctx.lineTo(a.right, py); ctx.stroke();
        if (h.label) { ctx.setLineDash([]); ctx.fillStyle = C.text; ctx.fillText(h.label, a.right - ctx.measureText(h.label).width - 4, py - 6); }
      }
      ctx.restore();
    },
  };

  const axis = (title, extra = {}) => ({
    title: { display: !!title, text: title, color: C.muted, font: { size: 11.5, weight: "500" } },
    grid: { color: C.grid, drawTicks: false }, border: { display: false },
    ticks: { color: C.muted, padding: 6, maxTicksLimit: 7 }, ...extra,
  });
  const charts = {};
  function makeChart(id, config) {
    if (charts[id]) charts[id].destroy();
    charts[id] = new Chart(document.getElementById(id), config);
    return charts[id];
  }

  // ------------------------------------------------------------ models
  const SKU_SEEDS = [11, 23, 37, 41, 53];
  const models = new Map();
  function modelFor(i) {
    if (!models.has(i)) {
      const sku = E.CATALOG[i];
      const hist = E.generateMarketHistory(sku, 365, SKU_SEEDS[i]);
      const fit = E.fitLogLogOls(hist.price, hist.comp, hist.qty, hist.dow);
      const [pStar] = E.unconstrainedOptimum(sku.unit_cogs, fit.elasticity_epsilon, sku.max_msrp);
      const qStar = E.expectedDemand(fit.alpha, fit.elasticity_epsilon, fit.cross_elasticity_beta, pStar, sku.base_price, 1, fit.smearing_factor);
      models.set(i, { sku, hist, fit, pStar, qStar });
    }
    return models.get(i);
  }
  function quoteFor(i, inventory, comp, surge) {
    const { sku, fit } = modelFor(i);
    return E.guardedQuote({
      unit_cogs: sku.unit_cogs, min_margin_pct: sku.min_margin_pct, max_msrp: sku.max_msrp, base_price: sku.base_price,
      epsilon: fit.elasticity_epsilon, beta: fit.cross_elasticity_beta, alpha: fit.alpha, smearing: fit.smearing_factor,
      inventory_level: inventory, competitor_price: comp, demand_surge_factor: surge,
      anchor_weight: POLICY.anchorWeight, min_days: POLICY.minDays, max_days: POLICY.maxDays, prior_epsilon: POLICY.priorEpsilon,
    });
  }

  function skuChips(container, onSelect, initial = 0) {
    container.innerHTML = E.CATALOG.map((s, i) =>
      `<button type="button" class="sku-chip" role="radio" aria-checked="${i === initial}" data-i="${i}">${esc(s.sku_name)}<small>${esc(s.sku_id)}</small></button>`
    ).join("");
    container.addEventListener("click", (ev) => {
      const b = ev.target.closest(".sku-chip"); if (!b) return;
      $$(".sku-chip", container).forEach((c) => c.setAttribute("aria-checked", String(c === b)));
      onSelect(+b.dataset.i);
    });
    container.addEventListener("keydown", (ev) => {
      if (!["ArrowRight", "ArrowLeft"].includes(ev.key)) return;
      const chips = $$(".sku-chip", container), cur = chips.findIndex((c) => c.getAttribute("aria-checked") === "true");
      const next = chips[(cur + (ev.key === "ArrowRight" ? 1 : chips.length - 1)) % chips.length];
      next.focus(); next.click();
    });
  }

  const STATUS_BADGE = {
    OPTIMAL_UNCONSTRAINED: ["good", "M3 8.5l3 3 7-7"],
    CLAMPED_TO_MARGIN_FLOOR: ["warning", "M8 3v6M8 12v1"],
    CLAMPED_TO_MSRP_CEILING: ["critical", "M8 3v6M8 12v1"],
  };
  function badge(text, kind, path) {
    const icon = path ? `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="${path}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>` : "";
    return `<span class="badge badge-${kind}">${icon}${esc(text)}</span>`;
  }
  const metric = (label, value, sub) => `<div><dt>${esc(label)}</dt><dd>${value}${sub ? `<small>${sub}</small>` : ""}</dd></div>`;

  // ============================================================ 1. SIMULATOR
  const sim = { i: 0, inventory: 0, comp: 0, compOn: true, surge: 1 };
  function simSetSku(i) {
    sim.i = i;
    const { sku, qStar } = modelFor(i);
    const inv = $("#inp-inventory"), comp = $("#inp-comp");
    inv.max = String(Math.max(50, Math.round(qStar * 90)));
    inv.step = "1";
    inv.value = String(Math.round(qStar * 21));
    comp.min = String(Math.round(sku.base_price * 0.5));
    comp.max = String(Math.round(sku.base_price * 1.5));
    comp.step = String(sku.base_price / 400);
    comp.value = String(sku.base_price);
    readSim(); renderSim(true);
  }
  function readSim() {
    sim.inventory = +$("#inp-inventory").value;
    sim.comp = +$("#inp-comp").value;
    sim.compOn = $("#inp-comp-on").checked;
    sim.surge = +$("#inp-surge").value;
  }
  function applyScenario(name) {
    const { sku, qStar } = modelFor(sim.i);
    const set = (id, v) => { $(id).value = String(v); };
    if (name === "normal") { set("#inp-inventory", Math.round(qStar * 21)); set("#inp-comp", sku.base_price); set("#inp-surge", 1); $("#inp-comp-on").checked = true; }
    if (name === "scarcity") { set("#inp-inventory", Math.round(qStar * 2)); set("#inp-comp", Math.round(sku.base_price * 1.05)); set("#inp-surge", 1.4); $("#inp-comp-on").checked = true; }
    if (name === "flood") { set("#inp-inventory", $("#inp-inventory").max); set("#inp-comp", $("#inp-comp").min); set("#inp-surge", 0.5); $("#inp-comp-on").checked = true; }
    if (name === "surge") { set("#inp-inventory", Math.round(qStar * 14)); set("#inp-comp", Math.round(sku.base_price * 1.1)); set("#inp-surge", 4); $("#inp-comp-on").checked = true; }
    readSim(); renderSim(false);
  }

  function renderSim(rebuild) {
    const { sku, fit } = modelFor(sim.i);
    const comp = sim.compOn ? sim.comp : null;
    const q = quoteFor(sim.i, sim.inventory, comp, sim.surge);
    ["#inp-inventory", "#inp-comp", "#inp-surge"].forEach((id) => setFill($(id)));
    $("#inp-comp").disabled = !sim.compOn;

    const qBase = E.expectedDemand(fit.alpha, q.pricing_elasticity, fit.cross_elasticity_beta, q.anchored_price, comp || sku.base_price, sim.surge, fit.smearing_factor);
    $("#out-inventory").textContent = nf(0).format(sim.inventory) + " units";
    $("#hint-inventory").textContent = `≈ ${num(sim.inventory / Math.max(qBase, 1e-9), 1)} days of cover at the anchored price. Band: ${POLICY.minDays} to ${POLICY.maxDays} days.`;
    $("#out-comp").textContent = sim.compOn ? usd(sim.comp) : "off";
    $("#out-surge").textContent = sim.surge.toFixed(2) + "×";

    $("#res-price").textContent = usd(q.recommended_price, 2);
    $("#res-price-sub").textContent = `${signedPct(q.recommended_price / sku.base_price - 1)} vs. static price ${usd(sku.base_price)} · ${sku.sku_name}`;
    const [kind, path] = STATUS_BADGE[q.guardrail_status];
    $("#res-badges").innerHTML = badge(q.guardrail_status, kind, path) + badge(q.inventory_regime, "info") + badge(q.optimization_regime, "info");

    $("#res-metrics").innerHTML = [
      metric("Lerner P*", usd(q.raw_optimal_price), `ε = ${num(q.pricing_elasticity, 3)}`),
      metric("Margin floor", usd(q.margin_floor_price), `markup ${pct(sku.min_margin_pct, 0)} = GM ${pct(q.floor_gross_margin_pct)}`),
      metric("MSRP ceiling", usd(q.max_msrp_ceiling)),
      metric("Gross margin", pct(q.expected_margin_pct), `${usd(q.expected_margin_usd)} per unit`),
      metric("Lerner index", num(q.lerner_index, 3), `theory −1/ε = ${num(q.theoretical_lerner_index, 3)}`),
      metric("Units / day", num(q.expected_daily_units, 1), "Duan-smeared E[Q]"),
      metric("Gross profit / day", usd(q.expected_daily_profit_usd)),
      metric("Days of cover", q.days_of_cover == null ? "—" : num(q.days_of_cover, 1), "at recommended price"),
      metric("Profit vs. static", signedPct(q.expected_profit_uplift_pct, 2), "ΔΠ% at constant ε"),
      metric("Break-even ΔQ", q.breakeven_volume_change_pct == null ? "—" : signedPct(q.breakeven_volume_change_pct), "volume to hold profit"),
    ].join("");

    renderLadder(q, sku);
    renderEnvelope(q, sku, fit, comp, rebuild);
    renderTrail(q, sku, fit, comp);
  }

  function renderLadder(q, sku) {
    const stages = [
      ["Unit COGS", "c", sku.unit_cogs],
      ["Margin floor", "c(1+m)", q.margin_floor_price],
      ["Lerner optimum", "cε/(1+ε)", q.raw_optimal_price],
      ["Competitor anchor", "P*^(1−w)·Pc^w", q.anchored_price],
      ["Sell-through band", "P(I/QT)^(1/ε)", q.inventory_adjusted_price],
      ["Guarded price", "min(MSRP, max(floor, P))", q.recommended_price],
    ];
    const lo = sku.unit_cogs * 0.92, hi = sku.max_msrp * 1.25;
    const pos = (v) => Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100));
    const bandL = pos(q.margin_floor_price), bandR = pos(q.max_msrp_ceiling);
    const ol = $("#ladder");
    if (!ol.children.length) {
      ol.innerHTML = stages.map(([n, f], idx) => `<li><div class="stage"><b>${esc(n)}</b><span>${esc(f)}</span></div><div class="track"><div class="band"></div><div class="dot${idx === stages.length - 1 ? " final" : ""}"></div></div><div class="val"></div></li>`).join("");
    }
    $$("li", ol).forEach((li, idx) => {
      const v = stages[idx][2];
      const band = $(".band", li); band.style.left = bandL + "%"; band.style.width = bandR - bandL + "%";
      $(".dot", li).style.left = pos(v) + "%";
      $(".val", li).textContent = (v > hi ? "› " : "") + usd(v);
      li.setAttribute("aria-label", `${stages[idx][0]}: ${usd(v, 2)}`);
    });
  }

  function profitCurve(sku, fit, eps, comp, surge, from, to, n = 180) {
    const pts = [];
    for (let k = 0; k <= n; k++) {
      const p = from + ((to - from) * k) / n;
      const qd = E.expectedDemand(fit.alpha, eps, fit.cross_elasticity_beta, p, comp || sku.base_price, surge, fit.smearing_factor);
      pts.push({ x: p, y: (p - sku.unit_cogs) * qd });
    }
    return pts;
  }

  function renderEnvelope(q, sku, fit, comp, rebuild) {
    const from = sku.unit_cogs, to = sku.max_msrp * 1.2;
    const curve = profitCurve(sku, fit, q.pricing_elasticity, comp, sim.surge, from, to);
    const recY = (q.recommended_price - sku.unit_cogs) * q.expected_daily_units;
    const guides = {
      bands: [{ from: q.margin_floor_price, to: q.max_msrp_ceiling, color: C.envelope }],
      vlines: [
        { value: q.margin_floor_price, color: C.warning, label: "floor", dash: [2, 3], offset: 14 },
        { value: q.max_msrp_ceiling, color: C.critical, label: "MSRP", dash: [2, 3], offset: 14 },
        { value: q.raw_optimal_price, color: C.s2, label: "P*", offset: 28 },
        { value: sku.base_price, color: C.base, label: "static", offset: 42 },
      ],
    };
    const ch = charts["chart-envelope"];
    if (ch && !rebuild) {
      ch.data.datasets[0].data = curve;
      ch.data.datasets[1].data = [{ x: q.recommended_price, y: recY }];
      ch.options.plugins.guides = guides;
      ch.options.scales.x.min = from; ch.options.scales.x.max = to;
      ch.update();
      return;
    }
    makeChart("chart-envelope", {
      type: "line",
      data: {
        datasets: [
          { label: "Gross profit / day", data: curve, borderColor: C.s1, backgroundColor: C.s1, tension: 0.25, parsing: false },
          { label: "Recommended price", data: [{ x: q.recommended_price, y: recY }], type: "scatter", pointRadius: 7, pointHoverRadius: 9, backgroundColor: C.emerald, borderColor: C.surface, borderWidth: 2, parsing: false },
        ],
      },
      options: {
        interaction: { mode: "nearest", axis: "x", intersect: false },
        scales: { x: axis("Price (USD)", { type: "linear", min: from, max: to, ticks: { color: C.muted, callback: (v) => compactUsd(v), maxTicksLimit: 7 } }), y: axis("Gross profit per day", { ticks: { color: C.muted, callback: (v) => compactUsd(v), maxTicksLimit: 6 } }) },
        plugins: {
          guides, crosshair: { enabled: true },
          legend: { position: "bottom" },
          tooltip: { callbacks: { title: (it) => "Price " + usd(it[0].parsed.x, 2), label: (it) => `${it.dataset.label}: ${usd(it.parsed.y)}` } },
        },
      },
    });
  }

  function renderTrail(q, sku, fit, comp) {
    const eps = q.pricing_elasticity, w = POLICY.anchorWeight;
    const steps = [];
    if (q.optimization_regime === E.REGIME.LERNER || q.optimization_regime === E.REGIME.PRIOR) {
      steps.push(["1 · Lerner optimum", `P^{*} = c\\,\\frac{\\varepsilon}{1+\\varepsilon} = ${texNum(sku.unit_cogs, 0)}\\times\\frac{${texNum(eps, 3)}}{${texNum(1 + eps, 3)}} = \\$${texNum(q.raw_optimal_price, 2)}`]);
    } else {
      steps.push(["1 · Inelastic corner", `\\varepsilon = ${texNum(eps, 3)} \\ge -1 \\Rightarrow P^{*} = \\text{MSRP} = \\$${texNum(q.raw_optimal_price, 2)}`]);
    }
    steps.push(comp
      ? ["2 · Competitor anchor", `P_{a} = P^{*\\,1-w}\\,P_{c}^{\\,w} = ${texNum(q.raw_optimal_price, 0)}^{${1 - w}}\\cdot ${texNum(comp, 0)}^{${w}} = \\$${texNum(q.anchored_price, 2)}`]
      : ["2 · Competitor anchor", `\\text{no competitor signal} \\Rightarrow P_{a} = P^{*} = \\$${texNum(q.anchored_price, 2)}`]);
    const qa = E.expectedDemand(fit.alpha, eps, fit.cross_elasticity_beta, q.anchored_price, comp || sku.base_price, sim.surge, fit.smearing_factor);
    const doc = sim.inventory / qa;
    let inv;
    if (q.inventory_regime === E.INVENTORY.STOCKOUT) inv = `I = 0 \\Rightarrow P_{i} = \\text{MSRP}`;
    else if (q.inventory_regime === E.INVENTORY.BALANCED) inv = `\\text{DoC} = \\frac{I}{Q(P_a)} = \\frac{${texNum(sim.inventory, 0)}}{${texNum(qa, 1)}} = ${texNum(doc, 1)} \\in [${POLICY.minDays}, ${POLICY.maxDays}] \\Rightarrow P_{i} = P_{a}`;
    else {
      const T = q.inventory_regime === E.INVENTORY.SCARCITY ? POLICY.minDays : POLICY.maxDays;
      inv = `\\text{DoC} = ${texNum(doc, 1)} \\Rightarrow P_{i} = P_{a}\\Big(\\frac{I}{Q\\,T}\\Big)^{1/\\varepsilon} = ${texNum(q.anchored_price, 0)}\\Big(\\frac{${texNum(sim.inventory, 0)}}{${texNum(qa, 1)}\\cdot ${T}}\\Big)^{1/${texNum(eps, 2)}} = \\$${texNum(q.inventory_adjusted_price, 2)}`;
    }
    steps.push(["3 · Inventory sell-through", inv]);
    steps.push(["4 · Circuit breaker", `P_{rec} = \\min\\big(${texNum(sku.max_msrp, 0)},\\ \\max(${texNum(q.margin_floor_price, 0)},\\ ${texNum(q.inventory_adjusted_price, 0)})\\big) = \\$${texNum(q.recommended_price, 2)}`]);
    const box = $("#formula-trail");
    if (box.children.length !== steps.length) box.innerHTML = steps.map(() => `<div class="trail-step"><p></p><div class="tx"></div></div>`).join("");
    $$(".trail-step", box).forEach((el, k) => { $("p", el).textContent = steps[k][0]; tex($(".tx", el), steps[k][1]); });
  }

  function initSim() {
    skuChips($("#sku-chips"), simSetSku);
    ["#inp-inventory", "#inp-comp", "#inp-surge", "#inp-comp-on"].forEach((id) =>
      $(id).addEventListener("input", () => { readSim(); renderSim(false); }));
    $$(".preset-row .chip-btn").forEach((b) => b.addEventListener("click", () => applyScenario(b.dataset.scenario)));
    simSetSku(0);
  }

  // ============================================================ 2. ELASTICITY
  let elIdx = 0;
  function renderElasticity() {
    const { sku, hist, fit, pStar } = modelFor(elIdx);
    const tc = fit.t_crit;
    const seBeta = fit.std_error_beta;
    const rows = [
      ["α intercept (avg. day)", fit.alpha, null, null, null, "—"],
      ["ε own-price", fit.elasticity_epsilon, fit.std_error_epsilon, fit.t_stat_epsilon,
        `[${num(fit.ci95_epsilon_low, 3)}, ${num(fit.ci95_epsilon_high, 3)}]`, num(sku.true_elasticity, 2)],
      ["β cross-price", fit.cross_elasticity_beta, seBeta, fit.cross_elasticity_beta / seBeta,
        `[${num(fit.cross_elasticity_beta - tc * seBeta, 3)}, ${num(fit.cross_elasticity_beta + tc * seBeta, 3)}]`, num(E.TRUE_CROSS_ELASTICITY, 2)],
    ];
    $("#coef-table tbody").innerHTML = rows.map((r) =>
      `<tr><td>${r[0]}</td><td class="num">${num(r[1], 4)}</td><td class="num">${r[2] == null ? "—" : num(r[2], 4)}</td><td class="num">${r[3] == null ? "—" : num(r[3], 1)}</td><td class="num">${r[4] || "—"}</td><td class="num">${r[5]}</td></tr>`).join("");
    $("#diag-metrics").innerHTML = [
      metric("R² / adj. R²", `${num(fit.r_squared, 3)} / ${num(fit.adj_r_squared, 3)}`),
      metric("p-value (ε)", fit.p_value_epsilon < 1e-12 ? "< 1e−12" : fit.p_value_epsilon.toExponential(2)),
      metric("RMSE (log)", num(fit.rmse_log, 4)),
      metric("Durbin-Watson", num(fit.durbin_watson, 3), "≈ 2: no autocorrelation"),
      metric("VIF (ln P)", num(fit.vif_log_price, 3), "< 5: no collinearity"),
      metric("Duan smearing S", num(fit.smearing_factor, 4), "E[Q] = S·e^ŷ"),
      metric("AIC / BIC", `${num(fit.aic, 0)} / ${num(fit.bic, 0)}`),
      metric("Observations", nf(0).format(fit.sample_size), "days, 9 parameters"),
    ].join("");

    // DOW- and competitor-adjusted scatter
    const lnBase = Math.log(sku.base_price);
    const pts = hist.price.map((p, k) => ({
      x: Math.log(p),
      y: Math.log(hist.qty[k]) - fit.dow_effects[hist.dow[k]] - fit.cross_elasticity_beta * (Math.log(hist.comp[k]) - lnBase),
    }));
    const xs = pts.map((p) => p.x), xmin = Math.min(...xs), xmax = Math.max(...xs);
    const line = [xmin, xmax].map((x) => ({ x, y: fit.alpha_monday + fit.elasticity_epsilon * x + fit.cross_elasticity_beta * lnBase }));
    makeChart("chart-loglog", {
      type: "scatter",
      data: { datasets: [
        { label: "Daily observation", data: pts, backgroundColor: "rgba(112,114,240,0.45)", borderColor: "rgba(112,114,240,0.8)", pointRadius: 3, pointHoverRadius: 6, order: 2 },
        { label: `OLS fit, slope ε = ${num(fit.elasticity_epsilon, 3)}`, data: line, type: "line", borderColor: C.s3, backgroundColor: C.s3, borderWidth: 3, pointRadius: 0, order: 1 },
      ] },
      options: {
        scales: { x: axis("ln P"), y: axis("adjusted ln Q") },
        plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { label: (it) => `ln P ${num(it.parsed.x, 3)} · ln Q ${num(it.parsed.y, 3)}` } } },
      },
    });

    const floor = E.marginFloorPrice(sku.unit_cogs, sku.min_margin_pct);
    const curve = profitCurve(sku, fit, fit.elasticity_epsilon, null, 1, sku.unit_cogs, sku.max_msrp * 1.25);
    makeChart("chart-profit", {
      type: "line",
      data: { datasets: [{ label: "Gross profit / day", data: curve, borderColor: C.s1, parsing: false, tension: 0.25 }] },
      options: {
        interaction: { mode: "nearest", axis: "x", intersect: false },
        scales: { x: axis("Price (USD)", { type: "linear", min: sku.unit_cogs, max: sku.max_msrp * 1.25, ticks: { color: C.muted, callback: (v) => compactUsd(v), maxTicksLimit: 7 } }), y: axis("Gross profit per day", { ticks: { color: C.muted, callback: (v) => compactUsd(v) } }) },
        plugins: {
          legend: { display: false }, crosshair: { enabled: true },
          guides: {
            bands: [{ from: floor, to: sku.max_msrp, color: C.envelope, label: "guardrail envelope" }],
            vlines: [{ value: pStar, color: C.s2, label: `P* = ${usd(pStar)}`, offset: 14 }, { value: sku.base_price, color: C.base, label: "static", offset: 28 }],
          },
          tooltip: { callbacks: { title: (it) => "Price " + usd(it[0].parsed.x, 2), label: (it) => "Gross profit/day " + usd(it.parsed.y) } },
        },
      },
    });

    const epsIn = $("#inp-eps");
    epsIn.value = String(Math.max(-6, Math.min(-1.1, fit.elasticity_epsilon)).toFixed(2));
    renderLerner();
  }

  function renderLerner() {
    const { sku, fit } = modelFor(elIdx);
    const epsIn = $("#inp-eps"); setFill(epsIn);
    const e = +epsIn.value, mult = e / (1 + e);
    $("#out-eps").textContent = `${e.toFixed(2)} → P*/c = ${mult.toFixed(3)} (${usd(sku.unit_cogs * mult)})`;
    const curve = [];
    for (let x = -6; x <= -1.1 + 1e-9; x += 0.02) curve.push({ x, y: x / (1 + x) });
    const ceilingMult = sku.max_msrp / sku.unit_cogs, floorMult = 1 + sku.min_margin_pct;
    makeChart("chart-lerner", {
      type: "line",
      data: { datasets: [
        { label: "P*/c = ε/(1+ε)", data: curve, borderColor: C.s2, parsing: false },
        { label: "Fitted ε", data: [{ x: fit.elasticity_epsilon, y: fit.elasticity_epsilon / (1 + fit.elasticity_epsilon) }], type: "scatter", pointRadius: 7, backgroundColor: C.s1, borderColor: C.surface, borderWidth: 2 },
        { label: "What-if ε", data: [{ x: e, y: mult }], type: "scatter", pointRadius: 7, pointStyle: "rectRot", backgroundColor: C.s3, borderColor: C.surface, borderWidth: 2 },
      ] },
      options: {
        animation: false,
        interaction: { mode: "nearest", intersect: false },
        scales: { x: axis("elasticity ε", { type: "linear", min: -6, max: -1.1 }), y: axis("P* / c", { type: "logarithmic", min: 1, max: 12, ticks: { color: C.muted, callback: (v) => ([1, 1.5, 2, 3, 5, 10].includes(v) ? v + "×" : "") } }) },
        plugins: {
          legend: { position: "bottom" },
          guides: { hlines: [{ value: ceilingMult, color: C.critical, label: `MSRP/c = ${ceilingMult.toFixed(2)}×` }, { value: floorMult, color: C.warning, label: `floor/c = ${floorMult.toFixed(2)}×` }] },
          tooltip: { callbacks: { label: (it) => `ε ${num(it.parsed.x, 2)} → P*/c ${num(it.parsed.y, 3)}` } },
        },
      },
    });
  }

  function initElasticity() {
    tex($(".tex-banner"), $(".tex-banner").dataset.tex);
    skuChips($("#el-chips"), (i) => { elIdx = i; renderElasticity(); });
    $("#inp-eps").addEventListener("input", renderLerner);
    renderElasticity();
  }

  // ============================================================ 3. BANDIT
  const ALGOS = [
    { key: "thompson_sampling", name: "Thompson Sampling", color: () => C.s1, tex: "\\theta_i \\sim \\mathcal{N}\\!\\left(\\bar x_i,\\ s^2/n_i\\right)" },
    { key: "ucb1", name: "UCB1", color: () => C.s2, tex: "\\bar x_i + s\\sqrt{2\\ln t / n_i}" },
    { key: "epsilon_greedy", name: "ε-Greedy", color: () => C.s3, tex: "\\Pr(\\text{explore}) = \\varepsilon" },
  ];
  function banditArms(i) {
    const { sku, fit, pStar } = modelFor(i);
    const floor = E.marginFloorPrice(sku.unit_cogs, sku.min_margin_pct);
    const raw = [0.85, 0.95, 1.0, 1.08, 1.18].map((m) => sku.base_price * m).filter((p) => Math.abs(p / pStar - 1) > 0.01);
    raw.push(pStar);
    const arms = Array.from(new Set(raw.map((p) => Math.round(E.clampToGuardrails(p, floor, sku.max_msrp)[0] * 100) / 100))).sort((a, b) => a - b);
    const lam = (p) => E.expectedDemand(fit.alpha, fit.elasticity_epsilon, fit.cross_elasticity_beta, p, sku.base_price, 1, fit.smearing_factor);
    return { sku, arms, units: arms.map(lam), baseUnits: lam(sku.base_price) };
  }
  function runArena(i, horizon, epsilon, seed) {
    const a = banditArms(i);
    return { ...a, results: ALGOS.map((al) => E.runBandit({
      arms: a.arms, expectedUnits: a.units, unitCogs: a.sku.unit_cogs, baselinePrice: a.sku.base_price, baselineUnits: a.baseUnits,
      horizon, algorithm: al.key, epsilon, seed, historyPoints: 80,
    })) };
  }

  /** Share of rounds on arms within 1% of the best arm's true expected profit (adjacent arms can be near-ties). */
  function nearOptimalShare(r) {
    const best = r.mu[r.best], total = r.counts.reduce((a, b) => a + b, 0);
    return r.counts.reduce((s, c, k) => s + (r.mu[k] >= 0.99 * best ? c : 0), 0) / total;
  }

  function renderArena() {
    const i = +$("#sel-bandit-sku").value, horizon = +$("#inp-horizon").value, epsilon = +$("#inp-epsilon").value;
    const seed = Math.max(0, Math.min(4294967295, Math.floor(+$("#inp-seed").value || 0)));
    const btn = $("#btn-bandit"); btn.disabled = true; btn.textContent = "Running…";
    setTimeout(() => {
      const arena = runArena(i, horizon, epsilon, seed);
      $("#algo-cards").innerHTML = arena.results.map((r, k) => `
        <article class="algo-card glass" style="--swatch:${ALGOS[k].color()}">
          <h3><span class="swatch" aria-hidden="true"></span>${ALGOS[k].name}</h3>
          <div class="tex-inline" data-tex="${esc(ALGOS[k].tex)}"></div>
          <dl>
            <div><dt>Regret cut vs static</dt><dd>${num(r.regretReductionPct, 1)}%</dd></div>
            <div><dt>Profit vs static</dt><dd>${r.profitUpliftPct >= 0 ? "+" : "−"}${num(Math.abs(r.profitUpliftPct), 2)}%</dd></div>
            <div><dt>Near-optimal share</dt><dd>${pct(nearOptimalShare(r))}</dd></div>
            <div><dt>Cum. regret</dt><dd>${compactUsd(r.regret)}</dd></div>
          </dl>
        </article>`).join("");
      $$("#algo-cards [data-tex]").forEach((el) => tex(el, el.dataset.tex, false));

      const steps = arena.results[0].history.map((h) => h.step);
      const reveal = (frac) => {
        const n = Math.max(2, Math.ceil(steps.length * frac));
        const regret = charts["chart-regret"], gap = charts["chart-profit-gap"];
        regret.data.labels = steps.slice(0, n); gap.data.labels = steps.slice(0, n);
        arena.results.forEach((r, k) => {
          regret.data.datasets[k].data = r.history.slice(0, n).map((h) => h.regret);
          gap.data.datasets[k].data = r.history.slice(0, n).map((h) => h.profit - h.staticProfit);
        });
        regret.data.datasets[3].data = arena.results[0].history.slice(0, n).map((h) => h.staticRegret);
        regret.update("none"); gap.update("none");
      };
      const lineSet = (extra) => ALGOS.map((al) => ({ label: al.name, data: [], borderColor: al.color(), backgroundColor: al.color() }))
        .concat(extra || []);
      const lineOpts = (yTitle) => ({
        animation: false, interaction: { mode: "index", intersect: false },
        scales: { x: axis("day", { ticks: { color: C.muted, maxTicksLimit: 6 } }), y: axis(yTitle, { ticks: { color: C.muted, callback: (v) => compactUsd(v) } }) },
        plugins: { legend: { position: "bottom" }, crosshair: { enabled: true }, tooltip: { callbacks: { title: (it) => "Day " + it[0].label, label: (it) => `${it.dataset.label}: ${usd(it.parsed.y)}` } } },
      });
      makeChart("chart-regret", { type: "line", data: { labels: [], datasets: lineSet([{ label: "Static price", data: [], borderColor: C.base, backgroundColor: C.base, borderDash: [6, 4] }]) }, options: lineOpts("cumulative regret") });
      const gapOpts = lineOpts("profit − static profit");
      gapOpts.plugins.guides = { hlines: [{ value: 0, color: C.base, label: "static baseline" }] };
      makeChart("chart-profit-gap", { type: "line", data: { labels: [], datasets: lineSet() }, options: gapOpts });

      if (reduceMotion) reveal(1);
      else {
        const t0 = performance.now(), dur = 1100;
        const tick = (t) => { const f = Math.min(1, (t - t0) / dur); reveal(f); if (f < 1) requestAnimationFrame(tick); };
        requestAnimationFrame(tick);
      }

      const labels = arena.arms.map((p) => usd(p));
      makeChart("chart-arms", {
        type: "bar",
        data: { labels, datasets: arena.results.map((r, k) => ({ label: ALGOS[k].name, data: r.counts, backgroundColor: ALGOS[k].color(), borderRadius: 4, borderSkipped: "start", borderColor: C.surface, borderWidth: { left: 1, right: 1 } })) },
        options: {
          interaction: { mode: "index", intersect: false },
          scales: { x: axis("candidate price", { grid: { display: false } }), y: axis("days pulled") },
          plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { label: (it) => `${it.dataset.label}: ${nf(0).format(it.parsed.y)} days` } } },
        },
      });
      const mu = arena.results[0].mu, best = arena.results[0].best;
      $("#arm-table thead").innerHTML = `<tr><th scope="col">Arm price</th><th scope="col" class="num">True E[profit]/day</th><th scope="col" class="num">Gap to best</th>${ALGOS.map((a) => `<th scope="col" class="num">${a.name}</th>`).join("")}</tr>`;
      $("#arm-table tbody").innerHTML = arena.arms.map((p, k) => `<tr><td>${usd(p, 2)}${k === best ? " ★" : ""}${Math.abs(p - arena.sku.base_price) < 0.01 ? " (static)" : ""}</td><td class="num">${usd(mu[k])}</td><td class="num">${k === best ? "—" : usd(mu[k] - mu[best])}</td>${arena.results.map((r) => `<td class="num">${nf(0).format(r.counts[k])}</td>`).join("")}</tr>`).join("");
      btn.disabled = false; btn.textContent = "Run arena";
    }, 30);
  }

  function initBandit() {
    $("#sel-bandit-sku").innerHTML = E.CATALOG.map((s, i) => `<option value="${i}">${esc(s.sku_name)} · ${esc(s.sku_id)}</option>`).join("");
    const outs = () => {
      $("#out-horizon").textContent = nf(0).format(+$("#inp-horizon").value) + " days";
      $("#out-epsilon").textContent = (+$("#inp-epsilon").value).toFixed(2);
      setFill($("#inp-horizon")); setFill($("#inp-epsilon"));
    };
    ["#inp-horizon", "#inp-epsilon"].forEach((id) => $(id).addEventListener("input", outs));
    $("#btn-bandit").addEventListener("click", renderArena);
    $("#sel-bandit-sku").addEventListener("change", renderArena);
    outs(); renderArena();
  }

  // ============================================================ 4. COHORTS
  let tenant = "tenant_alpha";
  function cohortRows() {
    const annual = +$("#inp-disc").value, cs = +$("#inp-churn").value, ks = +$("#inp-cac").value;
    const d = E.monthlyDiscountRate(annual);
    return E.generateCohorts(tenant).map((c) => {
      const churn = Math.min(0.95, c.churn * cs), cac = c.cac * ks;
      const ltv = E.ltvSimple(c.arpu, c.gm, churn), ltvd = E.ltvDiscounted(c.arpu, c.gm, churn, d);
      return { ...c, churn, cac, ltv, ltvd, ratio: ltv / cac, ratioD: ltvd / cac,
        payback: E.paybackSimple(cac, c.arpu, c.gm), paybackAdj: E.paybackChurnAdjusted(cac, c.arpu, c.gm, churn),
        annual: E.annualChurn(churn), half: E.halfLife(churn) };
    });
  }
  const ratioClass = (r) => (r >= 3 ? ["ratio-good", "healthy"] : r >= 1 ? ["ratio-warn", "thin"] : ["ratio-bad", "loss"]);

  function renderCohorts() {
    ["#inp-disc", "#inp-churn", "#inp-cac"].forEach((id) => setFill($(id)));
    $("#out-disc").textContent = pct(+$("#inp-disc").value, 0) + " / yr";
    $("#out-churn").textContent = (+$("#inp-churn").value).toFixed(2) + "×";
    $("#out-cac").textContent = (+$("#inp-cac").value).toFixed(2) + "×";
    const rows = cohortRows();
    const N = rows.reduce((s, r) => s + r.n, 0);
    const w = (f) => rows.reduce((s, r) => s + r.n * f(r), 0) / N;
    const ltv = w((r) => r.ltv), ltvd = w((r) => r.ltvd), cac = w((r) => r.cac), pb = w((r) => r.payback);
    const recovered = rows.filter((r) => r.paybackAdj != null);
    const pbAdj = recovered.length ? recovered.reduce((s, r) => s + r.n * r.paybackAdj, 0) / recovered.reduce((s, r) => s + r.n, 0) : null;
    const kpi = (label, value, sub) => `<article class="kpi glass"><p class="kpi-label">${label}</p><p class="kpi-value">${value}</p><p class="kpi-sub">${sub}</p></article>`;
    const [cls, word] = ratioClass(ltvd / cac);
    $("#cohort-kpis").innerHTML = [
      kpi("LTV / CAC", num(ltv / cac, 2) + "×", "undiscounted m/c, customer-weighted"),
      kpi("Discounted LTV / CAC", `<span class="ratio-pill ${cls}">${num(ltvd / cac, 2)}×</span>`, `${word} · benchmark ≥ 3×`),
      kpi("Weighted CAC", usd(cac), `${nf(0).format(N)} customers acquired`),
      kpi("Payback (simple → churn-adj.)", `${num(pb, 2)} → ${pbAdj == null ? "never" : num(pbAdj, 2)} mo`, `${rows.length - recovered.length} of 12 cohorts never recover CAC`),
    ].join("");

    $("#ledger tbody").innerHTML = rows.map((r) => {
      const [c, wd] = ratioClass(r.ratioD);
      return `<tr><td>${r.cohort_id}</td><td class="num">${nf(0).format(r.n)}</td><td class="num">${usd(r.arpu)}</td><td class="num">${pct(r.gm)}</td><td class="num">${pct(r.churn, 2)}</td><td class="num">${pct(r.annual)}</td><td class="num">${usd(r.cac)}</td><td class="num">${usd(r.ltv)}</td><td class="num">${usd(r.ltvd)}</td><td class="num"><span class="ratio-pill ${c}" title="${wd}">${num(r.ratioD, 2)}×</span></td><td class="num">${num(r.payback, 2)} mo</td><td class="num">${r.paybackAdj == null ? "never" : num(r.paybackAdj, 2) + " mo"}</td><td class="num">${num(r.half, 1)} mo</td></tr>`;
    }).join("");

    const labels = rows.map((r) => r.cohort_id);
    const ltvChart = charts["chart-ltv"];
    if (ltvChart) {
      ltvChart.data.datasets[0].data = rows.map((r) => r.ltvd);
      ltvChart.data.datasets[1].data = rows.map((r) => r.cac);
      ltvChart.update();
      charts["chart-ratio"].data.datasets[0].data = rows.map((r) => r.ratioD);
      charts["chart-ratio"].update();
      return;
    }
    makeChart("chart-ltv", {
      type: "bar",
      data: { labels, datasets: [
        { label: "Discounted LTV", data: rows.map((r) => r.ltvd), backgroundColor: C.s1, borderRadius: 4, borderSkipped: "start" },
        { label: "CAC", data: rows.map((r) => r.cac), backgroundColor: C.s3, borderRadius: 4, borderSkipped: "start" },
      ] },
      options: {
        interaction: { mode: "index", intersect: false },
        datasets: { bar: { categoryPercentage: 0.7, barPercentage: 0.9 } },
        scales: { x: axis("acquisition cohort", { grid: { display: false } }), y: axis("USD", { ticks: { color: C.muted, callback: (v) => compactUsd(v) } }) },
        plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { label: (it) => `${it.dataset.label}: ${usd(it.parsed.y)}` } } },
      },
    });
    makeChart("chart-ratio", {
      type: "line",
      data: { labels, datasets: [{ label: "Discounted LTV / CAC", data: rows.map((r) => r.ratioD), borderColor: C.s2, backgroundColor: C.s2, pointRadius: 4 }] },
      options: {
        interaction: { mode: "index", intersect: false },
        scales: { x: axis("acquisition cohort", { grid: { display: false } }), y: axis("ratio", { beginAtZero: true, ticks: { color: C.muted, callback: (v) => v + "×" } }) },
        plugins: { legend: { display: false }, crosshair: { enabled: true }, guides: { hlines: [{ value: 3, color: C.good, label: "3× benchmark" }, { value: 1, color: C.critical, label: "break-even" }] },
          tooltip: { callbacks: { label: (it) => `LTV_d / CAC: ${num(it.parsed.y, 2)}×` } } },
      },
    });
  }

  function initCohorts() {
    $$(".segmented button").forEach((b) => b.addEventListener("click", () => {
      tenant = b.dataset.tenant;
      $$(".segmented button").forEach((x) => x.setAttribute("aria-checked", String(x === b)));
      renderCohorts();
    }));
    ["#inp-disc", "#inp-churn", "#inp-cac"].forEach((id) => $(id).addEventListener("input", renderCohorts));
    renderCohorts();
  }

  // ============================================================ 5. FORMULAS
  const FORMULAS = [
    ["verified", "Price elasticity of demand", "\\varepsilon = \\frac{\\partial Q / Q}{\\partial P / P} = \\frac{\\partial \\ln Q}{\\partial \\ln P}", "Constant across price in a log-log model, so the OLS slope is the elasticity.", "elasticity_model.py"],
    ["corrected", "Log-log demand regression", "\\ln Q_t = \\alpha + \\varepsilon \\ln P_t + \\beta \\ln P^{c}_t + \\textstyle\\sum_d \\gamma_d D_{d,t} + u_t", "v2 adds day-of-week fixed effects (the seeded data has weekly seasonality v1 left in the error), lifting R² from about 0.4 to about 0.8.", "fit_log_log_ols"],
    ["verified", "Lerner profit-maximising price", "P^{*} = c\\,\\frac{\\varepsilon}{1+\\varepsilon},\\quad \\varepsilon < -1", "First-order condition of Π = (P − c)·A·P^ε. Verified numerically against a 200,000-point grid search.", "pricing_math.lerner_optimal_price"],
    ["new", "Lerner index (inverse-elasticity rule)", "L = \\frac{P - c}{P} = -\\frac{1}{\\varepsilon}", "Market-power diagnostic reported with every quote, next to the realised index after clamping.", "pricing_math.lerner_index"],
    ["corrected", "Inelastic demand: corner solution", "-1 \\le \\varepsilon < 0 \\;\\Rightarrow\\; \\frac{d\\Pi}{dP} > 0 \\;\\Rightarrow\\; P^{*} = \\text{MSRP}", "v1 substituted an arbitrary 1.5× markup. With inelastic demand profit rises with price, so the optimum is the ceiling.", "pricing_math.unconstrained_optimum"],
    ["verified", "Guarded recommended price", "P_{rec} = \\min\\big(\\text{MSRP},\\ \\max(c(1+m),\\ P)\\big)", "Non-bypassable. v2 also fails closed on NaN and refuses SKUs whose floor exceeds MSRP (HTTP 409).", "pricing_math.clamp_to_guardrails"],
    ["corrected", "Markup vs. gross margin", "g = \\frac{m}{1+m},\\qquad m = \\frac{g}{1-g}", "The floor c(1+m) is a markup on cost: a 22% floor is an 18.0% gross margin. v1 labelled both as “margin”.", "markup_to_gross_margin"],
    ["corrected", "Demand model used everywhere", "\\mathbb{E}[Q] = S\\cdot s\\cdot e^{\\alpha} P^{\\varepsilon} P_c^{\\beta}", "v1's bandit used e^{α + ε ln(P/P₀)}, dropping ε ln P₀ + β ln P_c: about 10⁷ units per day and USD 14 trillion profit in 50 rounds.", "pricing_math.expected_demand"],
    ["new", "Duan smearing retransformation", "S = \\frac{1}{n}\\sum_t e^{\\hat u_t} \\;\\ge 1", "e^{E[ln Q]} underestimates E[Q] (Jensen). Smearing removes the retransformation bias without assuming normal errors.", "smearing_factor"],
    ["corrected", "Competitor reference anchoring", "P_a = P^{*\\,1-w}\\,P_c^{\\,w},\\quad w = 0.30", "Under constant elasticity a competitor shift does not move P*; anchoring is an explicit, bounded policy (geometric mean), not a hidden heuristic.", "competitor_anchor"],
    ["corrected", "Inventory sell-through band", "P_i = P_a\\left(\\frac{I}{Q(P_a)\\,T}\\right)^{1/\\varepsilon},\\ T \\in \\{7, 45\\}", "Replaces v1's (500/I)^0.12 multiplier. Price moves only if days of cover leaves [7, 45], and lands exactly on the band edge.", "inventory_adjusted_price"],
    ["new", "Days of cover", "\\text{DoC} = \\frac{I}{\\mathbb{E}[Q(P)]}", "Links inventory and the surge factor to price through demand, consistently with the elasticity model.", "guarded_quote"],
    ["corrected", "Expected profit uplift", "\\Delta\\Pi\\% = \\frac{P_1 - c}{P_0 - c}\\left(\\frac{P_1}{P_0}\\right)^{\\varepsilon} - 1", "v1 stored the price change as “margin uplift”. Demand shifters cancel, so only ε is needed.", "profit_uplift_pct"],
    ["new", "Break-even volume for a price change", "\\Delta Q\\% = \\frac{-\\Delta P\\%}{CM\\% + \\Delta P\\%}", "How much volume a discount must win (or a price rise may lose) to keep gross profit constant.", "breakeven_volume_change"],
    ["new", "Arc (midpoint) elasticity", "\\varepsilon_{arc} = \\frac{(Q_2 - Q_1)/\\bar Q}{(P_2 - P_1)/\\bar P}", "Model-free check between two observed price points.", "arc_elasticity"],
    ["new", "OLS inference", "\\widehat{\\mathrm{Var}}(b) = s^2 (X'X)^{-1},\\ t = \\frac{b}{\\mathrm{se}},\\ b \\pm t_{0.975,n-k}\\,\\mathrm{se}", "Standard errors, t-statistics, p-values and 95% CIs for ε and β.", "fit_log_log_ols"],
    ["new", "Adjusted R²", "\\bar R^2 = 1 - (1 - R^2)\\frac{n-1}{n-k}", "Penalises the added fixed effects.", "adj_r_squared"],
    ["new", "Durbin-Watson", "d = \\frac{\\sum_t (\\hat u_t - \\hat u_{t-1})^2}{\\sum_t \\hat u_t^2}", "≈ 2 means no first-order autocorrelation, so the standard errors can be trusted.", "durbin_watson"],
    ["new", "Variance inflation factor", "\\mathrm{VIF}_{\\ln P} = \\frac{1}{1 - R^2_{aux}}", "Collinearity between own and competitor price; above 5 the elasticity is poorly identified.", "vif_log_price"],
    ["new", "Information criteria", "\\mathrm{AIC} = n\\ln\\frac{RSS}{n} + 2k,\\ \\ \\mathrm{BIC} = n\\ln\\frac{RSS}{n} + k\\ln n", "For comparing demand specifications.", "aic / bic"],
    ["corrected", "Thompson Sampling posterior", "\\theta_i \\sim \\mathcal{N}\\!\\left(\\bar x_i,\\ s^2 / n_i\\right)", "v1 seeded priors with 0.8 × the true arm profits (look-ahead leak) and shrank variance by an ad-hoc 1/(1+0.05n).", "run_bandit"],
    ["new", "UCB1 (scaled)", "a_t = \\arg\\max_i\\ \\bar x_i + s\\sqrt{\\frac{2\\ln t}{n_i}}", "Deterministic optimism as a third policy in the arena.", "run_bandit"],
    ["new", "Pseudo-regret", "R_T = \\sum_{t=1}^{T}\\left(\\mu^{*} - \\mu_{a_t}\\right),\\ \\mu_i = (P_i - c)\\,\\lambda(P_i)", "Measured against the true expected profit of each arm, for policies and the static price alike.", "cumulative_regret"],
    ["verified", "Customer lifetime value", "\\text{LTV} = \\sum_{t\\ge1} m(1-c)^{t-1} = \\frac{m}{c},\\ m = \\text{ARPU}\\cdot\\text{GM}", "Matches the spec; verified against the series sum.", "calculate_ltv"],
    ["new", "Discounted LTV", "\\text{LTV}_d = \\sum_{t\\ge1}\\frac{m(1-c)^{t-1}}{(1+d)^t} = \\frac{m}{c+d}", "Money has a time value; undiscounted LTV overstates long-lived cohorts.", "calculate_discounted_ltv"],
    ["new", "Monthly discount rate", "d_m = (1 + d_a)^{1/12} - 1", "Compound-equivalent, not d_a / 12.", "monthly_discount_rate"],
    ["verified", "LTV/CAC and simple payback", "\\frac{\\text{LTV}}{\\text{CAC}},\\qquad n = \\frac{\\text{CAC}}{m}", "Spec formulas, unchanged.", "calculate_payback_months"],
    ["corrected", "Portfolio LTV/CAC", "\\frac{\\sum_j N_j\\,\\text{LTV}_j}{\\sum_j N_j\\,\\text{CAC}_j}", "v1 averaged cohorts equally; v2 weights by customers acquired.", "get_tenant_cohorts"],
    ["new", "Churn-adjusted payback", "n = \\frac{\\ln\\left(1 - \\text{CAC}\\,c/m\\right)}{\\ln(1-c)}", "Survivors pay back CAC, not the whole cohort. Undefined (never) when CAC ≥ m/c.", "calculate_churn_adjusted_payback"],
    ["new", "Churn identities", "1-(1-c)^{12},\\quad \\mathbb{E}[T] = \\frac{1}{c},\\quad t_{1/2} = \\frac{\\ln 0.5}{\\ln(1-c)}", "Annual churn, expected lifetime and cohort half-life.", "annual_churn_rate"],
    ["corrected", "Robust anti-poisoning z-score", "z_i = \\frac{x_i - \\operatorname{median}(x)}{1.4826\\cdot\\operatorname{MAD}(x)},\\ |z_i| > 5", "v1 used mean/σ including the outliers, so a coordinated batch inflates σ and hides itself (masking).", "run_anti_poisoning_quality_gate"],
    ["new", "Fail-closed quarantine ratio", "\\frac{\\#\\text{quarantined}}{\\#\\text{events}} > 20\\% \\Rightarrow \\text{keep previous Silver}", "A mass poisoning attempt cannot be promoted by sheer volume.", "MAX_QUARANTINE_RATIO"],
  ];

  const AUDIT = [
    ["critical", "Authentication", "/api/v1/auth/token issued a valid JWT for any tenant with any secret of 6+ characters, so BOLA checks could be bypassed by asking for the other tenant's token.", "scrypt-hashed credential vault with constant-time verification and a dummy hash for unknown tenants; generic 401."],
    ["critical", "Secrets", "The HS256 signing key was hard-coded in config.py and .env.example, so anyone could forge tokens for any tenant.", "No default key: production refuses to start without one; the leaked key is deny-listed; development uses an ephemeral per-process key."],
    ["high", "Authorization", "Clients chose their own role claim in the token request.", "Role comes from the server-side vault; a role field is rejected with 422; role claims are allow-listed."],
    ["high", "Econometrics", "Bandit and dashboard demand used e^{α+ε ln(P/P₀)}: about 10⁷ units per day and USD 14 trillion in 50 rounds.", "One demand function, e^{α}P^{ε}P_c^{β} with smearing, shared by quote, bandit, charts and the JS engine."],
    ["high", "Operations", "run_local.py kept a read-write DuckDB lock open, so FastAPI and Streamlit both failed with “Could not set lock on file”.", "Preflight releases the lock; serving processes attach with DUCKDB_READ_ONLY=true."],
    ["high", "Input validation", "NaN, Infinity and 1e308 were accepted inside candidate_prices.", "allow_inf_nan=False on every model, bounded price items, identifier patterns on all IDs."],
    ["high", "Deployment", "The public local demo secrets were active in any non-production run, including a container bound to 0.0.0.0 with APP_ENV=development from .env.", "Demo secrets are refused unless HOST is loopback; docker compose pins APP_ENV=production for the API."],
    ["high", "Data quality", "Mean/σ z-score let coordinated poisoning mask itself.", "Median/MAD robust z-score, per-reason audit and a fail-closed quarantine ratio."],
    ["medium", "Experimentation", "Thompson Sampling priors were 0.8 × the true arm profits (look-ahead leak); no seed, so runs were not reproducible.", "Flat-prior Gaussian TS, Poisson demand, seeded generator, UCB1 added."],
    ["medium", "Econometrics", "With fewer than 3 rows the fit returned a fabricated R² of 0.85 and ε = −1.5.", "Documented prior ε = −2 with R² = 0, flagged is_prior_fallback."],
    ["medium", "Econometrics", "Pricing ignored day-of-week seasonality and the seeded elasticities put P* above MSRP for 3 of 5 SKUs.", "Day-of-week fixed effects; realistic elasticities so P* lands inside the envelope."],
    ["medium", "Gold layer", "expected_margin_uplift_pct stored the price change, not the profit change.", "Stores ΔΠ% from the closed form."],
    ["medium", "API hardening", "CORS * with credentials, exception text in 4xx/5xx bodies, 422 errors echoing secrets, no body-size limit.", "Origin allow-list, generic errors with server-side logs, redacted validation errors, 32 KB body cap, TrustedHost."],
    ["medium", "Dashboard", "The elasticity tab crashed: Plotly trendline='ols' needs statsmodels, which is not a dependency.", "Fit line drawn from the engine's own estimates; no dual-axis charts."],
    ["medium", "Testing", "Tests ran against, and rewrote, the binary DuckDB file committed to git.", "Throw-away database per session in conftest.py; the file is no longer tracked."],
    ["low", "Supply chain", "This page loaded an unpinned Chart.js from a CDN without SRI; hero KPIs were hard-coded.", "All assets self-hosted under script-src 'self'; every KPI is computed live."],
    ["low", "Containers", "Docker image ran as root and published ports on all interfaces.", "Non-root user, read-only root filesystem, no-new-privileges, loopback-only port bindings."],
  ];

  function initFormulas() {
    const grid = $("#formula-grid");
    grid.innerHTML = FORMULAS.map(([tag, title, t, desc, where]) => `
      <article class="formula-card glass" data-tag="${tag}">
        <header><h3>${esc(title)}</h3><span class="tag tag-${tag}">${tag}</span></header>
        <div class="tex" data-tex="${esc(t)}"></div>
        <p>${esc(desc)}</p>
        <code>${esc(where)}</code>
      </article>`).join("");
    $$("[data-tex]", grid).forEach((el) => tex(el, el.dataset.tex));
    const counts = FORMULAS.reduce((m, f) => ((m[f[0]] = (m[f[0]] || 0) + 1), m), {});
    $$(".filter-row .chip-btn").forEach((b) => {
      const f = b.dataset.filter;
      b.textContent += ` · ${f === "all" ? FORMULAS.length : counts[f] || 0}`;
      b.addEventListener("click", () => {
        $$(".filter-row .chip-btn").forEach((x) => x.classList.toggle("active", x === b));
        $$(".formula-card", grid).forEach((c) => { c.hidden = !(f === "all" || c.dataset.tag === f); });
      });
    });
    $("#audit-table tbody").innerHTML = AUDIT.map(([sev, area, v1, v2]) =>
      `<tr><td><span class="sev sev-${sev}">${sev.toUpperCase()}</span></td><td>${esc(area)}</td><td class="wrap">${esc(v1)}</td><td class="wrap">${esc(v2)}</td></tr>`).join("");
  }

  // ============================================================ 6. GATE DEMO
  function renderGate() {
    const k = +$("#inp-poison-k").value, m = +$("#inp-poison-m").value;
    ["#inp-poison-k", "#inp-poison-m"].forEach((id) => setFill($(id)));
    $("#out-poison-k").textContent = `${k} of 120`;
    $("#out-poison-m").textContent = m.toFixed(1) + "×";
    const rng = E.makeRng(2026), n = 120;
    const xs = [], poisoned = [];
    const slots = new Set();
    const prng = E.makeRng(99);
    while (slots.size < k) slots.add(prng.integer(n));
    for (let i = 0; i < n; i++) {
      const isP = slots.has(i);
      xs.push(isP ? 120 * m * rng.uniform(0.95, 1.05) : Math.max(1, Math.round(rng.normal(120, 15))));
      poisoned.push(isP);
    }
    const zc = E.classicZ(xs), zr = E.robustZ(xs), tol = POLICY.zTolerance;
    const caught = (z) => z.reduce((s, v, i) => s + (Math.abs(v) > tol && poisoned[i] ? 1 : 0), 0);
    const falsePos = (z) => z.reduce((s, v, i) => s + (Math.abs(v) > tol && !poisoned[i] ? 1 : 0), 0);
    const mean = xs.reduce((s, v) => s + v, 0) / n, sd = Math.sqrt(xs.reduce((s, v) => s + (v - mean) ** 2, 0) / n);
    const med = E.median(xs), rs = 1.4826 * E.mad(xs);
    const ratio = zr.filter((v) => Math.abs(v) > tol).length / n;
    const failed = ratio > POLICY.maxQuarantineRatio;
    $("#gate-metrics").innerHTML = [
      metric("Classic z caught", `${caught(zc)} / ${k}`, `threshold ${num(mean + tol * sd, 0)} units`),
      metric("Robust z caught", `${caught(zr)} / ${k}`, `threshold ${num(med + tol * rs, 0)} units`),
      metric("False positives", `${falsePos(zc)} classic · ${falsePos(zr)} robust`),
      metric("Gate status", failed ? `<span class="ratio-pill ratio-bad">FAILED_QUARANTINE_RATIO</span>` : `<span class="ratio-pill ratio-good">PASSED</span>`, `quarantine ratio ${pct(ratio)} (limit 20%)`),
    ].join("");
    const clean = [], bad = [];
    xs.forEach((v, i) => (Math.abs(zr[i]) > tol ? bad : clean).push({ x: i + 1, y: v }));
    const cfg = {
      type: "scatter",
      data: { datasets: [
        { label: "Passed to Silver", data: clean, backgroundColor: "rgba(138,148,166,0.75)", pointRadius: 4 },
        { label: "Quarantined by robust gate", data: bad, backgroundColor: C.critical, pointStyle: "triangle", pointRadius: 6 },
      ] },
      options: {
        animation: false,
        scales: { x: axis("event", { min: 0, max: n + 1 }), y: axis("units sold", { beginAtZero: true, max: Math.ceil(Math.max(...xs, mean + tol * sd, med + tol * rs) * 1.12 / 100) * 100 }) },
        plugins: {
          legend: { position: "bottom" },
          guides: { hlines: [
            { value: mean + tol * sd, color: C.warning, label: "classic mean + 5σ" },
            { value: med + tol * rs, color: C.good, dash: [], label: "robust median + 5·1.4826·MAD" },
          ] },
          tooltip: { callbacks: { label: (it) => `event ${it.parsed.x}: ${num(it.parsed.y, 0)} units` } },
        },
      },
    };
    const ch = charts["chart-gate"];
    if (ch) { ch.data = cfg.data; ch.options.plugins.guides = cfg.options.plugins.guides; ch.update(); }
    else makeChart("chart-gate", cfg);
  }
  function initGate() {
    ["#inp-poison-k", "#inp-poison-m"].forEach((id) => $(id).addEventListener("input", renderGate));
    renderGate();
  }

  // ============================================================ 7. SECURITY
  const OWASP = [
    ["API1:2023", "Broken Object Level Authorization", "Tenant comes from the verified JWT. A request naming another tenant gets 403 without echoing tenant IDs; another tenant's SKU inside your scope is a 404.", ["enforce_bola_isolation on every route", "parameterised tenant_id = ? on every query"]],
    ["API2:2023", "Broken Authentication", "Client secrets verified against scrypt hashes in constant time. Tokens are short-lived and fully validated.", ["alg pinned (rejects none)", "iss, aud, exp, nbf, iat, jti required", "10 requests/min on /auth/token"]],
    ["API3:2023", "Object Property Level Authorization", "Every request model forbids unknown fields and non-finite numbers, so mass-assignment and NaN/Inf poisoning fail at the edge.", ["extra='forbid', allow_inf_nan=False", "strict numeric bounds and ID patterns", "422 bodies never echo input"]],
    ["API4:2023", "Unrestricted Resource Consumption", "Simulation horizon capped at 5,000 rounds, request bodies at 32 KB, and per-route rate limits.", ["slowapi default + per-route limits", "BodySizeLimit middleware (413)"]],
    ["API5:2023", "Broken Function Level Authorization", "Roles are assigned server-side and allow-listed; a token with an unknown role is refused with 403.", ["pricing_analyst · growth_manager · revenue_admin"]],
    ["API8:2023", "Security Misconfiguration", "Strict headers on every response, CORS allow-list without credentials, TrustedHost, Swagger disabled in production.", ["CSP, HSTS, XFO DENY, nosniff, COOP, CORP", "Cache-Control: no-store on /api", "no Server header"]],
    ["Injection", "SQL injection", "DuckDB is only ever queried with ? bindings; table names come from a fixed allow-list.", ["identifier regex ^[A-Za-z0-9_-]{2,64}$"]],
    ["Algorithmic", "Pricing & data-poisoning safety", "Circuit breaker proven by 3,000-case fuzzing in tests and 1,000 cases at every preflight; robust z-score gate fails closed.", ["floor ≤ P ≤ MSRP for all inputs", "> 20% quarantined → Silver not promoted"]],
    ["API9:2023", "Improper Inventory Management", "One versioned surface (/api/v1). OpenAPI, Swagger and ReDoc are switched off when APP_ENV=production.", ["8 documented endpoints, no shadow routes", "probes exempt from auth, nothing else"]],
    ["Supply chain", "This page", "No third-party origins at all: fonts, KaTeX and Chart.js are self-hosted, enforced by a Content-Security-Policy.", ["script-src 'self'", "SHA-384 digests in assets/vendor/LICENSES.md"]],
  ];
  const ENDPOINTS = [
    ["GET", "/healthz", "none", "exempt"], ["GET", "/readyz", "none", "exempt"],
    ["POST", "/api/v1/auth/token", "client secret", "10/min"], ["GET", "/api/v1/catalog", "JWT", "120/min"],
    ["POST", "/api/v1/pricing/quote", "JWT + BOLA", "120/min"], ["GET", "/api/v1/pricing/elasticity", "JWT", "60/min"],
    ["POST", "/api/v1/simulation/bandit", "JWT + BOLA", "60/min"], ["GET", "/api/v1/economics/cohorts", "JWT + BOLA", "120/min"],
  ];
  function initSecurity() {
    $("#owasp-grid").innerHTML = OWASP.map(([code, title, desc, items]) => `
      <article class="owasp-card glass"><p class="code-tag">${esc(code)}</p><h3>${esc(title)}</h3><p>${esc(desc)}</p><ul>${items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul></article>`).join("");
    $("#endpoint-table tbody").innerHTML = ENDPOINTS.map(([m, p, a, l]) =>
      `<tr><td><span class="method method-${m.toLowerCase()}">${m}</span></td><td class="mono">${esc(p)}</td><td>${esc(a)}</td><td>${esc(l)}</td></tr>`).join("");
    fetch("assets/data/test-report.json", { cache: "no-cache" })
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((rep) => {
        $("#test-summary").textContent = `${rep.passed} / ${rep.total} tests passed · ${rep.python} · generated ${rep.generated_at.slice(0, 10)}`;
        $("#test-groups").innerHTML = rep.files.map((f, idx) => `
          <details${idx === 0 ? " open" : ""}><summary><span class="mono">${esc(f.file)}</span><span class="count">${f.passed}/${f.tests.length}</span></summary>
          <ul>${f.tests.map((t) => `<li class="${t.outcome === "passed" ? "" : "fail"}">${esc(t.name)}</li>`).join("")}</ul></details>`).join("");
      })
      .catch(() => { $("#test-summary").textContent = "Test report unavailable offline. Run pytest -v locally."; });
  }

  // ============================================================ HERO
  function initHero() {
    const rng = E.makeRng(31337);
    let violations = 0;
    const N = 10000, times = [];
    for (let k = 0; k < N; k++) {
      const i = rng.integer(E.CATALOG.length), { sku } = modelFor(i);
      const inv = [0, 1, Math.round(rng.uniform(0, 1e6))][rng.integer(3)];
      const comp = [null, 0.01, rng.uniform(0.01, 1e6)][rng.integer(3)];
      const t0 = performance.now();
      const q = quoteFor(i, inv, comp, rng.uniform(0.1, 10));
      times.push(performance.now() - t0);
      const floor = E.marginFloorPrice(sku.unit_cogs, sku.min_margin_pct);
      if (!(q.recommended_price >= floor - 1e-9 && q.recommended_price <= sku.max_msrp + 1e-9)) violations++;
    }
    times.sort((a, b) => a - b);
    $("#kpi-fuzz").textContent = String(violations);
    $("#kpi-fuzz-sub").textContent = `in ${nf(0).format(N)} adversarial quotes, computed on load`;
    const p95 = times[Math.floor(N * 0.95)];
    $("#kpi-latency").textContent = p95 < 0.01 ? "< 0.01 ms" : num(p95, 3) + " ms";
    const a = banditArms(0);
    const ts = E.runBandit({ arms: a.arms, expectedUnits: a.units, unitCogs: a.sku.unit_cogs, baselinePrice: a.sku.base_price, baselineUnits: a.baseUnits, horizon: 2000, algorithm: "thompson_sampling", seed: 7 });
    $("#kpi-regret").textContent = num(ts.regretReductionPct, 1) + "%";
    $("#kpi-regret-sub").textContent = `${a.sku.sku_name}, 2,000 days, seed 7`;
    const d = E.monthlyDiscountRate(POLICY.annualDiscountRate);
    const rows = E.generateCohorts("tenant_alpha");
    const N2 = rows.reduce((s, r) => s + r.n, 0);
    const ltvd = rows.reduce((s, r) => s + r.n * E.ltvDiscounted(r.arpu, r.gm, r.churn, d), 0) / N2;
    const cac = rows.reduce((s, r) => s + r.n * r.cac, 0) / N2;
    $("#kpi-ltv").textContent = num(ltvd / cac, 2) + "×";
  }

  // ============================================================ NAV
  function initNav() {
    const links = $$(".section-nav a");
    const byId = new Map(links.map((a) => [a.getAttribute("href").slice(1), a]));
    const io = new IntersectionObserver((entries) => {
      entries.forEach((en) => {
        if (en.isIntersecting && byId.has(en.target.id)) links.forEach((a) => a.classList.toggle("active", a === byId.get(en.target.id)));
      });
    }, { rootMargin: "-45% 0px -50% 0px" });
    $$("main section[id]").forEach((s) => io.observe(s));
  }

  function boot() {
    if (!window.Chart || !E) return;
    setupCharts();
    initNav();
    initHero();
    initSim();
    initElasticity();
    initBandit();
    initCohorts();
    initFormulas();
    initGate();
    initSecurity();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
