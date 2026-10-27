"""Streamlit Control Tower & Executive Cockpit for Real-Time Dynamic Pricing & Unit Economics."""

from __future__ import annotations
import math
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.lakehouse import lakehouse
from src.services.bandit_simulator import bandit_service
from src.services.elasticity_model import elasticity_service
from src.services.unit_economics import unit_economics_service

# Streamlit page configuration
st.set_page_config(
    page_title="Dynamic Pricing & Unit Economics Engine",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for modern enterprise aesthetics
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }
    
    .main-header {
        font-size: 2.2rem;
        font-weight: 800;
        background: linear-gradient(135deg, #10b981 0%, #06b6d4 50%, #6366f1 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.2rem;
    }
    
    .sub-header {
        font-size: 1.05rem;
        color: #94a3b8;
        margin-bottom: 1.5rem;
    }
    
    .metric-card {
        background: rgba(30, 41, 59, 0.7);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 12px;
        padding: 1.2rem;
        box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
        backdrop-filter: blur(8px);
        margin-bottom: 1rem;
    }
    
    .badge-optimal {
        background-color: rgba(16, 185, 129, 0.2);
        color: #10b981;
        border: 1px solid #10b981;
        padding: 4px 12px;
        border-radius: 20px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    
    .badge-floor {
        background-color: rgba(245, 158, 11, 0.2);
        color: #f59e0b;
        border: 1px solid #f59e0b;
        padding: 4px 12px;
        border-radius: 20px;
        font-weight: 600;
        font-size: 0.85rem;
    }
    
    .badge-ceiling {
        background-color: rgba(239, 68, 68, 0.2);
        color: #ef4444;
        border: 1px solid #ef4444;
        padding: 4px 12px;
        border-radius: 20px;
        font-weight: 600;
        font-size: 0.85rem;
    }
</style>
""", unsafe_allow_html=True)

# Sidebar
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
    - **Engine**: FastAPI 0.115+
    - **Lakehouse**: DuckDB Medallion
    - **Security**: OWASP Top 10 + BOLA
    - **Econometrics**: Log-Log OLS & Thompson Sampling
    """)
    st.markdown("---")
    st.caption("Real-Time Dynamic Pricing & Unit Economics Engine v1.0.0")

# Header
st.markdown('<div class="main-header">⚡ Real-Time Dynamic Pricing & Unit Economics Engine</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Automated Medallion Lakehouse, Econometric Elasticity, OWASP Guardrails & Multi-Armed Bandit Experimentation</div>', unsafe_allow_html=True)

# Tabs
tab_quote, tab_elasticity, tab_bandit, tab_economics, tab_lakehouse = st.tabs([
    "🎯 Real-Time Pricing Quote",
    "📈 Econometric Elasticity Studio",
    "🎰 Bandit Experimentation Arena",
    "💎 Cohort Unit Economics & LTV/CAC",
    "🏛️ Medallion Lakehouse & Quality Gates",
])

# Retrieve SKU catalog for selected tenant
skus = lakehouse.get_sku_catalog(tenant_id)
sku_map = {s["sku_id"]: s for s in skus}

# ==============================================================================
# TAB 1: REAL-TIME PRICING QUOTE
# ==============================================================================
with tab_quote:
    st.subheader("🎯 Real-Time Guarded Optimal Pricing Quote")
    st.markdown("Inference latency target: **<50ms P95**. Automatically clamps to non-bypassable margin floor and MSRP ceiling.")

    if not skus:
        st.warning("No SKUs available. Please run lakehouse seed script.")
    else:
        col_input, col_result = st.columns([1, 1.4])

        with col_input:
            st.markdown("#### Input Parameters")
            selected_sku_id = st.selectbox(
                "Select SKU",
                list(sku_map.keys()),
                format_func=lambda x: f"{x} - {sku_map[x]['sku_name']}",
            )
            sku_info = sku_map[selected_sku_id]

            inventory_val = st.slider(
                "Real-Time Inventory Level (Units)",
                min_value=10,
                max_value=1000,
                value=420,
                step=10,
                help="Low inventory introduces a scarcity markup; high inventory discounts towards margin floor.",
            )

            comp_price = st.number_input(
                "Competitor Price ($ USD)",
                min_value=1.0,
                max_value=float(sku_info["max_msrp"] * 2.0),
                value=float(sku_info["base_price"]),
                step=10.0,
            )

            demand_surge = st.slider(
                "Market Demand Surge Factor",
                min_value=0.5,
                max_value=3.0,
                value=1.0,
                step=0.1,
                help="Demand surge multiplier (1.0 = Normal, 2.0 = High Surge).",
            )

        with col_result:
            st.markdown("#### Guarded Pricing Engine Inference")
            quote = elasticity_service.compute_guarded_quote(
                tenant_id=tenant_id,
                sku_id=selected_sku_id,
                inventory_level=inventory_val,
                competitor_price=comp_price,
                demand_surge_factor=demand_surge,
            )

            rec_p = quote["recommended_price"]
            raw_p = quote["raw_optimal_price"]
            floor_p = quote["margin_floor_price"]
            ceiling_p = quote["max_msrp_ceiling"]
            status = quote["guardrail_status"]

            # Status Badge
            badge_class = (
                "badge-optimal" if status == "OPTIMAL_UNCONSTRAINED"
                else "badge-floor" if status == "CLAMPED_TO_MARGIN_FLOOR"
                else "badge-ceiling"
            )
            st.markdown(f'Status: <span class="{badge_class}">{status}</span>', unsafe_allow_html=True)
            st.markdown(f"### **Recommended Price: ${rec_p:,.2f}**")

            m_col1, m_col2, m_col3 = st.columns(3)
            with m_col1:
                st.metric("Raw P* (Lerner)", f"${raw_p:,.2f}")
                st.metric("Unit COGS", f"${quote['unit_cogs']:,.2f}")
            with m_col2:
                st.metric("Margin Floor", f"${floor_p:,.2f}")
                st.metric("Gross Margin ($)", f"${quote['expected_margin_usd']:,.2f}")
            with m_col3:
                st.metric("MSRP Ceiling", f"${ceiling_p:,.2f}")
                st.metric("Gross Margin (%)", f"{quote['expected_margin_pct']*100:.1f}%")

            # Price Comparison Bar
            fig_bar = go.Figure(data=[
                go.Bar(
                    name="Price Points",
                    x=["Unit COGS", "Margin Floor", "Recommended Price", "Raw Optimal P*", "MSRP Ceiling"],
                    y=[quote["unit_cogs"], floor_p, rec_p, raw_p, ceiling_p],
                    marker_color=["#64748b", "#f59e0b", "#10b981", "#6366f1", "#ef4444"],
                    text=[f"${v:,.0f}" for v in [quote["unit_cogs"], floor_p, rec_p, raw_p, ceiling_p]],
                    textposition="auto",
                )
            ])
            fig_bar.update_layout(
                title="Price Architecture & Guardrail Envelope",
                template="plotly_dark",
                height=280,
                margin=dict(l=20, r=20, t=40, b=20),
            )
            st.plotly_chart(fig_bar, use_container_width=True)

# ==============================================================================
# TAB 2: ECONOMETRIC ELASTICITY STUDIO
# ==============================================================================
with tab_elasticity:
    st.subheader("📈 Econometric Log-Log OLS Elasticity Studio")
    st.markdown(r"Governing Equation: $\ln(Q) = \alpha + \epsilon \cdot \ln(P) + \beta \cdot \ln(P_{\text{comp}})$")

    fit_sku = st.selectbox(
        "Select SKU to Inspect Demand Elasticity Curve",
        list(sku_map.keys()),
        key="elasticity_sku_select",
        format_func=lambda x: f"{x} - {sku_map[x]['sku_name']}",
    )

    fit_stats = elasticity_service.fit_sku_elasticity(tenant_id, fit_sku)

    e_col1, e_col2, e_col3, e_col4 = st.columns(4)
    with e_col1:
        st.metric("Price Elasticity (ε)", f"{fit_stats['elasticity_epsilon']:.4f}")
    with e_col2:
        st.metric("Cross Elasticity (β)", f"{fit_stats['cross_elasticity_beta']:.4f}")
    with e_col3:
        st.metric("Goodness of Fit (R²)", f"{fit_stats['r_squared']:.4f}")
    with e_col4:
        st.metric("Historical Data Points", f"{fit_stats['sample_size']}")

    # Fetch daily demand observations from Silver
    with lakehouse.get_connection(read_only=False) as conn:
        obs_df = conn.execute("""
            SELECT date_bucket, avg_price, total_qty, unit_cogs, max_msrp
            FROM silver_validated_demand
            WHERE tenant_id = ? AND sku_id = ?
            ORDER BY date_bucket ASC
        """, [tenant_id, fit_sku]).fetchdf()

    if not obs_df.empty:
        c_curve1, c_curve2 = st.columns(2)

        with c_curve1:
            fig_scatter = px.scatter(
                obs_df,
                x="avg_price",
                y="total_qty",
                title=f"Empirical Price vs Daily Quantity Demanded ({fit_sku})",
                labels={"avg_price": "Observed Price ($)", "total_qty": "Daily Units Sold"},
                trendline="ols",
                template="plotly_dark",
            )
            fig_scatter.update_traces(marker=dict(size=6, color="#06b6d4", opacity=0.7))
            fig_scatter.update_layout(height=360, margin=dict(l=20, r=20, t=40, b=20))
            st.plotly_chart(fig_scatter, use_container_width=True)

        with c_curve2:
            # Theoretical Profit Curve
            cogs = obs_df["unit_cogs"].iloc[0]
            msrp = obs_df["max_msrp"].iloc[0]
            price_range = np.linspace(cogs * 1.05, msrp * 1.05, 100)
            alpha = fit_stats.get("alpha", 8.0)
            eps = fit_stats["elasticity_epsilon"]

            base_p = sku_map[fit_sku]["base_price"]
            profits = []
            revenues = []
            for p in price_range:
                q = math.exp(alpha + eps * math.log(p / base_p))
                revenues.append(p * q)
                profits.append((p - cogs) * q)

            p_star_raw = cogs * (eps / (1.0 + eps)) if eps < -1.0 else cogs * 1.4

            fig_profit = go.Figure()
            fig_profit.add_trace(go.Scatter(x=price_range, y=profits, mode="lines", name="Expected Profit ($)", line=dict(color="#10b981", width=3)))
            fig_profit.add_vline(x=p_star_raw, line_dash="dash", line_color="#6366f1", annotation_text=f"P* = ${p_star_raw:,.0f}")
            fig_profit.update_layout(
                title="Profit Optimization Curve vs Price",
                xaxis_title="Price ($)",
                yaxis_title="Expected Profit ($)",
                template="plotly_dark",
                height=360,
                margin=dict(l=20, r=20, t=40, b=20),
            )
            st.plotly_chart(fig_profit, use_container_width=True)

# ==============================================================================
# TAB 3: BANDIT EXPERIMENTATION ARENA
# ==============================================================================
with tab_bandit:
    st.subheader("🎰 Multi-Armed Bandit Price Experimentation Arena")
    st.markdown("Simulate **Thompson Sampling (Bayesian)** and **Epsilon-Greedy** online learning against a static price baseline.")

    b_col1, b_col2, b_col3, b_col4 = st.columns(4)
    with b_col1:
        bandit_sku = st.selectbox("Select Experiment SKU", list(sku_map.keys()), key="bandit_sku_select")
    with b_col2:
        bandit_algo = st.selectbox("Bandit Algorithm", ["thompson_sampling", "epsilon_greedy"])
    with b_col3:
        horizon_val = st.slider("Simulation Horizon (Rounds)", min_value=100, max_value=2000, value=600, step=50)
    with b_col4:
        eps_param = st.slider("Epsilon Exploration Rate", min_value=0.01, max_value=0.50, value=0.10, step=0.01) if bandit_algo == "epsilon_greedy" else 0.10

    if st.button("🚀 Run Live Price Experimentation", type="primary"):
        with st.spinner("Simulating multi-armed bandit pricing dynamics..."):
            sim_res = bandit_service.run_simulation(
                tenant_id=tenant_id,
                sku_id=bandit_sku,
                horizon=horizon_val,
                algorithm=bandit_algo,
                epsilon_param=eps_param,
            )

        kpi1, kpi2, kpi3, kpi4 = st.columns(4)
        with kpi1:
            st.metric("Bandit Cumulative Profit", f"${sim_res['bandit_profit']:,.2f}", f"+{sim_res['profit_uplift_pct']:.2f}% uplift")
        with kpi2:
            st.metric("Static Baseline Profit", f"${sim_res['baseline_profit']:,.2f}")
        with kpi3:
            st.metric("Cumulative Regret", f"${sim_res['cumulative_regret']:,.2f}")
        with kpi4:
            st.metric("Revenue Uplift", f"+{sim_res['revenue_uplift_pct']:.2f}%")

        if sim_res.get("step_history"):
            hist_df = pd.DataFrame(sim_res["step_history"])

            fig_cum = go.Figure()
            fig_cum.add_trace(go.Scatter(x=hist_df["step"], y=hist_df["cum_bandit_profit"], mode="lines", name="Bandit Profit", line=dict(color="#10b981", width=3)))
            fig_cum.add_trace(go.Scatter(x=hist_df["step"], y=hist_df["cum_static_profit"], mode="lines", name="Static Baseline", line=dict(color="#94a3b8", dash="dot")))
            fig_cum.update_layout(
                title=f"Cumulative Profit Over Time ({bandit_algo.replace('_', ' ').title()})",
                xaxis_title="Simulation Step / Day",
                yaxis_title="Cumulative Profit ($)",
                template="plotly_dark",
                height=340,
                margin=dict(l=20, r=20, t=40, b=20),
            )
            st.plotly_chart(fig_cum, use_container_width=True)

        # Arm Allocation Distribution
        alloc_df = pd.DataFrame(list(sim_res["arm_allocations"].items()), columns=["Price Arm", "Rounds Selected"])
        fig_alloc = px.bar(
            alloc_df,
            x="Price Arm",
            y="Rounds Selected",
            title="Arm Allocation Distribution (Exploration vs Exploitation)",
            color="Rounds Selected",
            color_continuous_scale="Viridis",
            template="plotly_dark",
        )
        fig_alloc.update_layout(height=280, margin=dict(l=20, r=20, t=40, b=20))
        st.plotly_chart(fig_alloc, use_container_width=True)

# ==============================================================================
# TAB 4: COHORT UNIT ECONOMICS & LTV/CAC
# ==============================================================================
with tab_economics:
    st.subheader("💎 Cohort Unit Economics & LTV to CAC Engine")
    st.markdown(r"Governing Equation: $\text{LTV} = \frac{\text{ARPU} \times \text{Gross Margin \%}}{\text{Monthly Churn Rate}}, \quad \text{LTV/CAC} = \frac{\text{LTV}}{\text{CAC}}$")

    cohort_data = unit_economics_service.get_tenant_cohorts(tenant_id)

    u_col1, u_col2, u_col3, u_col4 = st.columns(4)
    with u_col1:
        st.metric("Aggregate LTV / CAC Ratio", f"{cohort_data['aggregate_ltv_to_cac']:.2f}x", "Benchmark > 3.0x")
    with u_col2:
        st.metric("Average Cohort LTV", f"${cohort_data['aggregate_ltv_usd']:,.2f}")
    with u_col3:
        st.metric("Average Blended CAC", f"${cohort_data['aggregate_cac_usd']:,.2f}")
    with u_col4:
        st.metric("CAC Payback Period", f"{cohort_data['avg_payback_months']:.1f} Months", "Target < 12 mos")

    if cohort_data["cohorts"]:
        c_df = pd.DataFrame(cohort_data["cohorts"])

        fig_ltv_cac = go.Figure()
        fig_ltv_cac.add_trace(go.Bar(x=c_df["cohort_id"], y=c_df["ltv_usd"], name="Cohort LTV ($)", marker_color="#10b981"))
        fig_ltv_cac.add_trace(go.Bar(x=c_df["cohort_id"], y=c_df["cac_usd"], name="Cohort CAC ($)", marker_color="#f59e0b"))
        fig_ltv_cac.update_layout(
            barmode="group",
            title="Cohort LTV vs CAC Dynamics (12 Month Trend)",
            xaxis_title="Acquisition Cohort Month",
            yaxis_title="USD ($)",
            template="plotly_dark",
            height=340,
            margin=dict(l=20, r=20, t=40, b=20),
        )
        st.plotly_chart(fig_ltv_cac, use_container_width=True)

        st.markdown("#### Cohort Unit Economics Ledger")
        st.dataframe(
            c_df.style.format({
                "arpu_usd": "${:,.2f}",
                "gross_margin_pct": "{:.1%}",
                "churn_rate": "{:.2%}",
                "cac_usd": "${:,.2f}",
                "ltv_usd": "${:,.2f}",
                "ltv_cac_ratio": "{:.2f}x",
                "payback_months": "{:.1f} mos",
            }),
            use_container_width=True,
        )

# ==============================================================================
# TAB 5: MEDALLION LAKEHOUSE & QUALITY GATES
# ==============================================================================
with tab_lakehouse:
    st.subheader("🏛️ DuckDB Medallion Lakehouse & OWASP Quality Gates")
    st.markdown("Automated Medallion Architecture with **z-score > 5.0 anti-poisoning quarantine**.")

    counts = lakehouse.get_table_counts()

    l_col1, l_col2, l_col3, l_col4 = st.columns(4)
    with l_col1:
        st.metric("Bronze Raw Market Events", f"{counts.get('bronze_market_events', 0):,}")
    with l_col2:
        st.metric("Silver Validated Daily Demand", f"{counts.get('silver_validated_demand', 0):,}")
    with l_col3:
        st.metric("Gold SKU Elasticity Models", f"{counts.get('gold_sku_elasticity', 0):,}")
    with l_col4:
        st.metric("Gold Cohort Records", f"{counts.get('gold_cohort_economics', 0):,}")

    st.markdown("#### Quality Gate Audit Trail")
    with lakehouse.get_connection(read_only=False) as conn:
        gate_df = conn.execute("""
            SELECT gate_id, run_timestamp, tenant_id, total_bronze_records, passed_silver_records, rejected_poison_records, status
            FROM audit_quality_gates
            ORDER BY run_timestamp DESC
            LIMIT 10
        """, []).fetchdf()

    if not gate_df.empty:
        st.dataframe(gate_df, use_container_width=True)
    else:
        st.info("No quality gate runs logged yet.")
