# Architectural Decision Records (ADR)

## Project: Real-Time Dynamic Pricing & Unit Economics Engine
**Status**: APPROVED  
**Date**: October 2026  
**Architect**: Principal Data Engineer, Econometrician, and Secure API Architect

---

### ADR-001: Embedded DuckDB Medallion Lakehouse Storage Architecture

#### Context
Digital marketplace pricing engines require fast analytical queries across historical price-quantity transactions without introducing the operational overhead of external database daemons (e.g., PostgreSQL, ClickHouse, or Snowflake) during local development and embedded edge deployments.

#### Decision
Adopt an embedded **DuckDB Medallion Lakehouse** (`Bronze`, `Silver`, `Gold` tables):
1. **Bronze (`bronze_market_events`)**: Append-only ingestion of raw timestamped price, competitor price, inventory, and transaction volume signals.
2. **Silver (`silver_validated_demand`)**: Automated promotion layer enforcing an **OWASP Anti-Poisoning Gate** (rejecting $|z| > 5.0$ price/quantity spikes) and aggregating into daily SKU demand buckets.
3. **Gold (`gold_sku_elasticity`, `gold_cohort_economics`)**: Econometrically fitted elasticity coefficients ($\epsilon, \beta, R^2$), guarded price benchmarks, and cohort LTV/CAC economics.

#### Concurrency & Locking Strategy
- In Python runtime, manage a single re-entrant connection with `threading.RLock()` to ensure concurrent threads within the FastAPI process execute without configuration collision.
- The Streamlit cockpit queries the Lakehouse cleanly or through the FastAPI REST layer to eliminate process file-lock contention.

---

### ADR-002: Econometric Log-Log OLS Demand Elasticity & Lerner Index Pricing

#### Context
Static pricing causes margin leakage when market conditions surge or inventory depletes. However, naive machine learning regressors (e.g., random forests or deep nets) can produce erratic, unexplainable pricing swings and violate microeconomic monotonicity.

#### Decision
Implement classical econometric **Log-Log Ordinary Least Squares (OLS)** formulation:
$$\ln(Q) = \alpha + \epsilon \cdot \ln(P) + \beta \cdot \ln(P_{\text{comp}}) + \gamma \cdot \text{Scarcity}$$

- **Own-Price Elasticity of Demand**: $\epsilon = \frac{\partial Q / Q}{\partial P / P}$.
- **Optimal Unconstrained Price ($P^*$)**: Derived from profit maximization $\Pi(P) = (P - \text{COGS}) \cdot Q(P)$:
  $$P^* = \text{COGS} \cdot \left(\frac{\epsilon}{1 + \epsilon}\right) \quad \text{for } \epsilon < -1.0$$
- If demand is inelastic ($\epsilon \ge -1.0$), $P^*$ defaults to target gross markup.

---

### ADR-003: Non-Bypassable Hard Margin Floor & MSRP Guardrail Circuit Breaker

#### Context
Unbounded pricing algorithms risk selling products below unit cost during black swan anomalies or predatory competitor pricing, or gouging above statutory maximum retail price (MSRP) ceilings.

#### Decision
Enforce a non-bypassable post-processing mathematical circuit breaker:
$$P_{\text{rec}} = \min\left(\text{max\_msrp}, \max\left(\text{unit\_cogs} \cdot (1 + \text{min\_margin\_pct}), P^*\right)\right)$$

- **Margin Floor Circuit Breaker**: Under no circumstance can $P_{\text{rec}} < \text{unit\_cogs} \cdot (1 + \text{min\_margin\_pct})$.
- **MSRP Ceiling**: Prevents runaway algorithmic escalation during artificial supply shocks.
- Returns explicit audit state: `OPTIMAL_UNCONSTRAINED`, `CLAMPED_TO_MARGIN_FLOOR`, or `CLAMPED_TO_MSRP_CEILING`.

---

### ADR-004: Multi-Armed Bandit Experimentation (Thompson Sampling vs Epsilon-Greedy)

#### Context
Static A/B testing incurs substantial opportunity cost (regret) by directing equal traffic to sub-optimal price points for weeks.

#### Decision
Implement **Thompson Sampling (Bayesian)** and **Epsilon-Greedy** Multi-Armed Bandit (MAB) simulation:
- Discrete price arms around the baseline price envelope.
- Reward defined as contribution profit: $\Pi(P) = (P - \text{COGS}) \cdot Q_t$.
- Thompson Sampling samples posterior distributions of expected profit per arm, rapidly concentrating traffic on the profit-maximizing price arm while preserving continuous exploration.
- Capped at `max_horizon=5000` per API invocation to comply with OWASP API4 Resource Consumption limits.

---

### ADR-005: OWASP API Security & Defense-in-Depth Controls

#### Context
Multi-tenant enterprise pricing APIs contain sensitive gross margins, COGS, and customer cohort economics vulnerable to competitive reconnaissance and data exfiltration.

#### Decision
Enforce the OWASP API Security Top 10 controls:
1. **OWASP API1 (BOLA)**: Strict tenant isolation (`enforce_bola_isolation`) verifying that the authenticated JWT `tenant_id` matches the request payload. Cross-tenant access returns HTTP 403 Forbidden.
2. **OWASP API3 (Mass Assignment Prevention)**: All Pydantic v2 request schemas enforce `model_config = ConfigDict(extra='forbid')` and strict numeric bounds (`gt=0`, `le=1000000`, `allow_inf_nan=False`).
3. **OWASP API4 (Resource Consumption)**: Rate limiting via `slowapi` and strict cap on simulation horizon ($N \le 5000$).
4. **SQL Injection Prevention**: Parameterized queries exclusively on DuckDB bindings.
5. **Security Headers**: Injects `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Content-Security-Policy: default-src 'self'`, and `Strict-Transport-Security`.
