"""Streamlit Control Tower & Executive Cockpit for Real-Time Dynamic Pricing & Unit Economics.

Local operator console: binds to 127.0.0.1 and attaches to the lakehouse read-only.
"""

from __future__ import annotations

import html

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.lakehouse import lakehouse
from src.services import pricing_math as pm
from src.services.bandit_simulator import bandit_service
from src.services.elasticity_model import elasticity_service
from src.services.unit_economics import unit_economics_service

EMERALD, CYAN, INDIGO, AMBER, ROSE, SLATE = "#10b981", "#06b6d4", "#6366f1", "#f59e0b", "#f43f5e", "#94a3b8"

st.set_page_config(
    page_title="Dynamic Pricing & Unit Economics Engine",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    html, body, [class*="css"] { font-family: 'Inter', system-ui, sans-serif; }
    .main-header {
        font-size: 2.1rem; font-weight: 800; margin-bottom: 0.2rem;
        background: linear-gradient(135deg, #10b981 0%, #06b6d4 50%, #6366f1 100%);
        -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    }
    .sub-header { font-size: 1.0rem; color: #94a3b8; margin-bottom: 1.2rem; }
    .badge { padding: 4px 12px; border-radius: 20px; font-weight: 600; font-size: 0.85rem; border: 1px solid; }
    .badge-optimal { background: rgba(16,185,129,.15); color: #10b981; border-color: #10b981; }
    .badge-floor { background: rgba(245,158,11,.15); color: #f59e0b; border-color: #f59e0b; }
    .badge-ceiling { background: rgba(244,63,94,.15); color: #f43f5e; border-color: #f43f5e; }
    .badge-info { background: rgba(99,102,241,.15); color: #a5b4fc; border-color: #6366f1; }
</style>
""", unsafe_allow_html=True)


def badge(text: str, kind: str) -> str:
    return f'<span class="badge badge-{kind}">{html.escape(text)}</span>'


def dark_layout(fig: go.Figure, title: str, height: int = 340) -> go.Figure:
    fig.update_layout(title=title, template="plotly_dark", height=height, margin=dict(l=20, r=20, t=48, b=20),
                      legend=dict(orientation="h", y=-0.2))
    return fig


with st.sidebar:
    st.markdown("### 🏢 Tenant & Workspace")
    tenant_id = st.selectbox(
        "Active Tenant Organization",
        ["tenant_alpha", "tenant_beta"],
        format_func=lambda x: "Tenant Alpha (High-Tech & GPU)" if x == "tenant_alpha" else "Tenant Beta (Industrial IoT)",
    )
    st.markdown("---")
    st.markdown("### 🛡️ System Architecture")
    st.markdown("""
    - **Engine**: FastAPI + Pydantic v2
    - **Lakehouse**: DuckDB Medallion (read-only attach)
    - **Security**: OWASP API Top 10, scrypt vault, JWT aud/iss/jti
    - **Econometrics**: Log-log OLS + DOW, Lerner, sell-through band
    - **Experimentation**: Thompson Sampling, UCB1, ε-Greedy
    """)
    st.markdown("---")
    st.caption("Real-Time Dynamic Pricing & Unit Economics Engine v2.0.0")

st.markdown('<div class="main-header">⚡ Real-Time Dynamic Pricing & Unit Economics Engine</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Medallion Lakehouse, econometric elasticity, non-bypassable guardrails and bandit experimentation</div>', unsafe_allow_html=True)

tab_quote, tab_elasticity, tab_bandit, tab_economics, tab_lakehouse = st.tabs([
    "🎯 Real-Time Pricing Quote",
    "📈 Econometric Elasticity Studio",
    "🎰 Bandit Experimentation Arena",
    "💎 Cohort Unit Economics & LTV/CAC",
    "🏛️ Medallion Lakehouse & Quality Gates",
])

skus = lakehouse.get_sku_catalog(tenant_id)
sku_map = {s["sku_id"]: s for s in skus}


def sku_label(sku_id: str) -> str:
    return f"{sku_id} - {sku_map[sku_id]['sku_name']}"


# ==============================================================================
# TAB 1: REAL-TIME PRICING QUOTE
# ==============================================================================
with tab_quote:
    st.subheader("🎯 Real-Time Guarded Optimal Pricing Quote")
    st.markdown("Pipeline: **Lerner optimum → competitor anchor → inventory sell-through band → margin floor / MSRP circuit breaker**.")

    if not skus:
        st.warning("No SKUs available. Run `python run_local.py --check` to seed the lakehouse.")
    else:
        col_input, col_result = st.columns([1, 1.5])
        with col_input:
            selected_sku_id = st.selectbox("Select SKU", list(sku_map.keys()), format_func=sku_label)
            sku_info = sku_map[selected_sku_id]
            fit_q = elasticity_service.fit_sku_elasticity(tenant_id, selected_sku_id)
            q_star = pm.expected_demand(fit_q["alpha"], min(fit_q["elasticity_epsilon"], -1.01),
                                        fit_q["cross_elasticity_beta"], fit_q["raw_optimal_price"],
                                        sku_info["base_price"], 1.0, fit_q["smearing_factor"])
            inv_max = int(max(100, q_star * 90))
            inventory_val = st.slider("Real-Time Inventory Level (Units)", 0, inv_max, int(q_star * 21),
                                      step=max(1, inv_max // 200),
                                      help="Days of cover below 7 adds a scarcity premium, above 45 triggers a markdown.")
            use_comp = st.toggle("Use competitor price signal", value=True)
            comp_price = st.number_input("Competitor Price ($ USD)", min_value=1.0,
                                         max_value=float(sku_info["max_msrp"] * 2.0),
                                         value=float(sku_info["base_price"]), step=10.0, disabled=not use_comp)
            demand_surge = st.slider("Market Demand Surge Factor", 0.1, 5.0, 1.0, 0.1)

        with col_result:
            quote = elasticity_service.compute_guarded_quote(
                tenant_id=tenant_id, sku_id=selected_sku_id, inventory_level=inventory_val,
                competitor_price=comp_price if use_comp else None, demand_surge_factor=demand_surge,
            )
            status = quote["guardrail_status"]
            kind = {"OPTIMAL_UNCONSTRAINED": "optimal", "CLAMPED_TO_MARGIN_FLOOR": "floor"}.get(status, "ceiling")
            st.markdown(f"{badge(status, kind)} &nbsp; {badge(quote['inventory_regime'], 'info')} &nbsp; "
                        f"{badge(quote['optimization_regime'], 'info')}", unsafe_allow_html=True)
            st.markdown(f"### Recommended Price: **${quote['recommended_price']:,.2f}**")

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Lerner P*", f"${quote['raw_optimal_price']:,.2f}")
            m2.metric("Margin Floor", f"${quote['margin_floor_price']:,.2f}", f"GM {quote['floor_gross_margin_pct']:.1%}")
            m3.metric("MSRP Ceiling", f"${quote['max_msrp_ceiling']:,.2f}")
            m4.metric("Lerner Index", f"{quote['lerner_index']:.3f}", f"theory {quote['theoretical_lerner_index']:.3f}")
            m5, m6, m7, m8 = st.columns(4)
            m5.metric("Expected Units / Day", f"{quote['expected_daily_units']:,.1f}")
            m6.metric("Gross Profit / Day", f"${quote['expected_daily_profit_usd']:,.0f}")
            m7.metric("Days of Cover", "—" if quote["days_of_cover"] is None else f"{quote['days_of_cover']:.1f}")
            m8.metric("Profit Uplift vs Base", f"{quote['expected_profit_uplift_pct']:+.2%}")

            wf = quote["pricing_waterfall"]
            fig_bar = go.Figure(go.Bar(
                x=[w["price"] for w in wf], y=[w["stage"] for w in wf], orientation="h",
                marker_color=[SLATE, AMBER, INDIGO, CYAN, "#a78bfa", EMERALD],
                text=[f"${w['price']:,.0f}  ·  {w['formula']}" for w in wf], textposition="auto",
            ))
            fig_bar.add_vline(x=quote["max_msrp_ceiling"], line_dash="dot", line_color=ROSE, annotation_text="MSRP")
            fig_bar.update_yaxes(autorange="reversed")
            st.plotly_chart(dark_layout(fig_bar, "Price Architecture Waterfall & Guardrail Envelope", 320), use_container_width=True)

# ==============================================================================
# TAB 2: ECONOMETRIC ELASTICITY STUDIO
# ==============================================================================
with tab_elasticity:
    st.subheader("📈 Econometric Log-Log OLS Elasticity Studio")
    st.markdown(r"$\ln Q_t = \alpha + \varepsilon \ln P_t + \beta \ln P^{comp}_t + \sum_d \gamma_d D_{d,t} + u_t$"
                r" $\qquad P^* = c\,\frac{\varepsilon}{1+\varepsilon}$")

    if skus:
        fit_sku = st.selectbox("Select SKU", list(sku_map.keys()), key="elasticity_sku_select", format_func=sku_label)
        fs = elasticity_service.fit_sku_elasticity(tenant_id, fit_sku)
        e1, e2, e3, e4, e5, e6 = st.columns(6)
        e1.metric("Elasticity ε", f"{fs['elasticity_epsilon']:.3f}", f"95% CI [{fs['ci95_epsilon_low']:.2f}, {fs['ci95_epsilon_high']:.2f}]")
        e2.metric("Cross elasticity β", f"{fs['cross_elasticity_beta']:.3f}", f"SE {fs['std_error_beta']:.3f}")
        e3.metric("R² / adj R²", f"{fs['r_squared']:.3f}", f"adj {fs['adj_r_squared']:.3f}")
        e4.metric("t-stat (ε)", f"{fs['t_stat_epsilon']:.1f}", f"p = {fs['p_value_epsilon']:.1e}")
        e5.metric("Durbin-Watson", f"{fs['durbin_watson']:.2f}", f"VIF {fs['vif_log_price']:.2f}")
        e6.metric("Duan smearing", f"{fs['smearing_factor']:.4f}", f"n = {fs['sample_size']}")

        with lakehouse.get_connection(read_only=True) as conn:
            obs_df = conn.execute("""
                SELECT date_bucket, avg_price, avg_comp_price, total_qty, unit_cogs, max_msrp, min_margin_pct
                FROM silver_validated_demand
                WHERE tenant_id = ? AND sku_id = ?
                ORDER BY date_bucket ASC
            """, [tenant_id, fit_sku]).fetchdf()

        if not obs_df.empty:
            c1, c2 = st.columns(2)
            eps, beta, alpha, smear = fs["elasticity_epsilon"], fs["cross_elasticity_beta"], fs["alpha"], fs["smearing_factor"]
            base_p = sku_map[fit_sku]["base_price"]
            with c1:
                lp = np.log(obs_df["avg_price"].to_numpy())
                grid = np.linspace(lp.min(), lp.max(), 50)
                fit_line = alpha + eps * grid + beta * np.log(base_p)
                fig_sc = go.Figure()
                fig_sc.add_trace(go.Scatter(x=lp, y=np.log(obs_df["total_qty"]), mode="markers", name="ln Q (daily)",
                                            marker=dict(size=5, color=CYAN, opacity=0.6)))
                fig_sc.add_trace(go.Scatter(x=grid, y=fit_line, mode="lines", name=f"OLS fit (slope ε = {eps:.2f})",
                                            line=dict(color=EMERALD, width=3)))
                fig_sc.update_xaxes(title="ln P"); fig_sc.update_yaxes(title="ln Q")
                st.plotly_chart(dark_layout(fig_sc, "Log-Log Demand Curve"), use_container_width=True)
            with c2:
                cogs, msrp = obs_df["unit_cogs"].iloc[0], obs_df["max_msrp"].iloc[0]
                floor = pm.margin_floor_price(cogs, obs_df["min_margin_pct"].iloc[0])
                prices = np.linspace(cogs * 1.02, msrp * 1.1, 160)
                q = np.array([pm.expected_demand(alpha, eps, beta, p, base_p, 1.0, smear) for p in prices])
                fig_pf = go.Figure()
                fig_pf.add_vrect(x0=floor, x1=msrp, fillcolor=EMERALD, opacity=0.07, line_width=0,
                                 annotation_text="guardrail envelope", annotation_position="top left")
                fig_pf.add_trace(go.Scatter(x=prices, y=(prices - cogs) * q, name="Gross profit / day", line=dict(color=EMERALD, width=3),
                                            customdata=prices * q, hovertemplate="P $%{x:,.0f}<br>profit $%{y:,.0f}<br>revenue $%{customdata:,.0f}<extra></extra>"))
                fig_pf.add_vline(x=fs["raw_optimal_price"], line_dash="dash", line_color=AMBER,
                                 annotation_text=f"P* = ${fs['raw_optimal_price']:,.0f}")
                fig_pf.update_xaxes(title="Price ($)"); fig_pf.update_yaxes(title="Gross profit ($)")
                st.plotly_chart(dark_layout(fig_pf, "Profit Optimization Curve vs Price"), use_container_width=True)

# ==============================================================================
# TAB 3: BANDIT EXPERIMENTATION ARENA
# ==============================================================================
with tab_bandit:
    st.subheader("🎰 Multi-Armed Bandit Price Experimentation Arena")
    st.markdown("Gaussian **Thompson Sampling**, **UCB1** and **ε-Greedy** against a static price, Poisson demand from the fitted curve.")

    if skus:
        b1, b2, b3, b4, b5 = st.columns(5)
        bandit_sku = b1.selectbox("Experiment SKU", list(sku_map.keys()), key="bandit_sku_select")
        bandit_algo = b2.selectbox("Algorithm", ["thompson_sampling", "ucb1", "epsilon_greedy"])
        horizon_val = b3.slider("Horizon (days)", 100, 5000, 1000, 50)
        eps_param = b4.slider("ε exploration", 0.01, 0.5, 0.10, 0.01, disabled=bandit_algo != "epsilon_greedy")
        seed_val = b5.number_input("Seed", 0, 2**31 - 1, 7)

        if st.button("🚀 Run Live Price Experimentation", type="primary"):
            sim = bandit_service.run_simulation(tenant_id, bandit_sku, horizon=horizon_val, algorithm=bandit_algo,
                                                epsilon_param=eps_param, seed=int(seed_val))
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Bandit Profit", f"${sim['bandit_profit']:,.0f}", f"{sim['profit_uplift_pct']:+.2f}% vs static")
            k2.metric("Static Baseline Profit", f"${sim['baseline_profit']:,.0f}", f"P₀ = ${sim['baseline_price']:,.2f}")
            k3.metric("Cumulative Regret", f"${sim['cumulative_regret']:,.0f}", f"-{sim['regret_reduction_pct']:.1f}% vs static",
                      delta_color="inverse")
            k4.metric("Optimal Arm Share", f"{sim['optimal_arm_share']:.1%}", f"P = ${sim['optimal_arm_price']:,.2f}")

            hist_df = pd.DataFrame(sim["step_history"])
            h1, h2 = st.columns(2)
            with h1:
                fig_c = go.Figure()
                fig_c.add_trace(go.Scatter(x=hist_df["step"], y=hist_df["cum_bandit_profit"], name="Bandit", line=dict(color=EMERALD, width=3)))
                fig_c.add_trace(go.Scatter(x=hist_df["step"], y=hist_df["cum_static_profit"], name="Static", line=dict(color=SLATE, dash="dot")))
                st.plotly_chart(dark_layout(fig_c, "Cumulative Gross Profit"), use_container_width=True)
            with h2:
                fig_r = go.Figure()
                fig_r.add_trace(go.Scatter(x=hist_df["step"], y=hist_df["cum_regret"], name="Bandit regret", line=dict(color=CYAN, width=3)))
                fig_r.add_trace(go.Scatter(x=hist_df["step"], y=hist_df["cum_static_regret"], name="Static regret", line=dict(color=ROSE, dash="dot")))
                st.plotly_chart(dark_layout(fig_r, "Cumulative Pseudo-Regret"), use_container_width=True)

            alloc = pd.DataFrame({
                "arm": list(sim["arm_allocations"].keys()),
                "pulls": list(sim["arm_allocations"].values()),
                "true_mu": list(sim["arm_true_expected_profit"].values()),
            })
            fig_a = go.Figure(go.Bar(x=alloc["arm"], y=alloc["pulls"], marker_color=CYAN, name="Pulls",
                                     customdata=alloc["true_mu"],
                                     hovertemplate="%{x}<br>%{y} days<br>true E[profit]/day $%{customdata:,.0f}<extra></extra>"))
            st.plotly_chart(dark_layout(fig_a, "Arm Allocation (hover for true expected profit)", 300), use_container_width=True)
            st.dataframe(alloc.rename(columns={"arm": "Arm price", "pulls": "Days pulled", "true_mu": "True E[profit]/day"}),
                         use_container_width=True, hide_index=True)

# ==============================================================================
# TAB 4: COHORT UNIT ECONOMICS & LTV/CAC
# ==============================================================================
with tab_economics:
    st.subheader("💎 Cohort Unit Economics & LTV to CAC Engine")
    st.markdown(r"$\text{LTV} = \frac{m}{c},\ \text{LTV}_d = \frac{m}{c + d},\ m = \text{ARPU}\cdot\text{GM},"
                r"\ n_{payback} = \frac{\ln(1 - \text{CAC}\,c/m)}{\ln(1-c)}$")

    cd = unit_economics_service.get_tenant_cohorts(tenant_id)
    u1, u2, u3, u4, u5 = st.columns(5)
    u1.metric("LTV / CAC (customer-weighted)", f"{cd['aggregate_ltv_to_cac']:.2f}x", "Benchmark > 3.0x")
    u2.metric("Discounted LTV / CAC", f"{cd['aggregate_discounted_ltv_to_cac']:.2f}x", f"d = {cd['annual_discount_rate']:.0%}/yr")
    u3.metric("Weighted LTV", f"${cd['aggregate_ltv_usd']:,.0f}")
    u4.metric("Weighted CAC", f"${cd['aggregate_cac_usd']:,.0f}")
    u5.metric("Simple Payback", f"{cd['avg_payback_months']:.1f} mo", f"{cd['total_new_customers']:,} customers")

    if cd["cohorts"]:
        c_df = pd.DataFrame(cd["cohorts"])
        fig_lc = go.Figure()
        fig_lc.add_trace(go.Bar(x=c_df["cohort_id"], y=c_df["ltv_usd"], name="LTV (undiscounted)", marker_color=EMERALD))
        fig_lc.add_trace(go.Bar(x=c_df["cohort_id"], y=c_df["discounted_ltv_usd"], name="LTV (discounted)", marker_color=CYAN))
        fig_lc.add_trace(go.Bar(x=c_df["cohort_id"], y=c_df["cac_usd"], name="CAC", marker_color=AMBER))
        fig_lc.update_layout(barmode="group")
        st.plotly_chart(dark_layout(fig_lc, "Cohort LTV vs CAC (12 Month Trend)"), use_container_width=True)

        st.markdown("#### Cohort Unit Economics Ledger")
        cols = ["cohort_id", "new_customers", "arpu_usd", "gross_margin_pct", "churn_rate", "annual_churn_rate",
                "cac_usd", "ltv_usd", "discounted_ltv_usd", "ltv_cac_ratio", "payback_months",
                "churn_adjusted_payback_months", "customer_half_life_months"]
        st.dataframe(
            c_df[cols].style.format({
                "arpu_usd": "${:,.2f}", "gross_margin_pct": "{:.1%}", "churn_rate": "{:.2%}",
                "annual_churn_rate": "{:.1%}", "cac_usd": "${:,.0f}", "ltv_usd": "${:,.0f}",
                "discounted_ltv_usd": "${:,.0f}", "ltv_cac_ratio": "{:.2f}x", "payback_months": "{:.1f}",
                "churn_adjusted_payback_months": "{:.2f}", "customer_half_life_months": "{:.1f}",
            }, na_rep="never"),
            use_container_width=True,
        )

# ==============================================================================
# TAB 5: MEDALLION LAKEHOUSE & QUALITY GATES
# ==============================================================================
with tab_lakehouse:
    st.subheader("🏛️ DuckDB Medallion Lakehouse & OWASP Quality Gates")
    st.markdown(r"Robust anti-poisoning gate: $z_i = \frac{x_i - \mathrm{median}(x)}{1.4826\cdot\mathrm{MAD}(x)}$, "
                r"quarantine if $|z_i| > 5$; the gate fails closed when more than 20% of a batch is quarantined.")

    counts = lakehouse.get_table_counts()
    l1, l2, l3, l4 = st.columns(4)
    l1.metric("Bronze Raw Market Events", f"{counts.get('bronze_market_events', 0):,}")
    l2.metric("Silver Validated Daily Demand", f"{counts.get('silver_validated_demand', 0):,}")
    l3.metric("Gold SKU Elasticity Models", f"{counts.get('gold_sku_elasticity', 0):,}")
    l4.metric("Gold Cohort Records", f"{counts.get('gold_cohort_economics', 0):,}")

    with lakehouse.get_connection(read_only=True) as conn:
        gate_df = conn.execute("""
            SELECT gate_id, run_timestamp, tenant_id, total_bronze_records, passed_silver_records,
                   rejected_poison_records, quarantine_ratio, status
            FROM audit_quality_gates
            WHERE tenant_id = ?
            ORDER BY run_timestamp DESC
            LIMIT 10
        """, [tenant_id]).fetchdf()
        quarantine_df = conn.execute("""
            SELECT event_id, sku_id, timestamp, observed_price, competitor_price, units_sold, rejection_reason
            FROM bronze_market_events
            WHERE tenant_id = ? AND is_rejected_poison = TRUE
            ORDER BY timestamp DESC
            LIMIT 50
        """, [tenant_id]).fetchdf()

    st.markdown("#### Quality Gate Audit Trail")
    if gate_df.empty:
        st.info("No quality gate runs logged yet.")
    else:
        st.dataframe(gate_df, use_container_width=True)
    st.markdown("#### Quarantined Bronze Events")
    st.dataframe(quarantine_df, use_container_width=True)
