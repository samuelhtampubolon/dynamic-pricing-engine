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

---

### ADR-006 (v2): Corrected pricing pipeline and explicit policy parameters

#### Context
The v1 quote multiplied the Lerner price by an inventory factor `(500/I)^0.12` and a surge factor `s^(1/|ε|)`, and blended competitor prices linearly. Under a constant-elasticity demand curve neither a demand shift nor a competitor price changes the unconstrained optimum, so these factors were unexplained heuristics. The inelastic branch used an arbitrary markup, and the bandit used a demand intercept that produced about 10^7 units per day.

#### Decision
- One demand function everywhere: `E[Q] = S·s·exp(α)·P^ε·Pc^β`, with Duan smearing `S`.
- Inelastic demand is a corner solution at MSRP.
- Competitor influence is an explicit geometric anchor with weight `w = 0.30` (`COMPETITOR_ANCHOR_WEIGHT`).
- Inventory and surge act through days of cover: price moves only if DoC leaves `[7, 45]` days, to the price that lands exactly on the band edge.
- The pure formulas live in `src/services/pricing_math.py`; the GitHub Pages engine is parity-tested against them.

### ADR-007 (v2): Read-only serving attach for DuckDB

#### Context
DuckDB allows one read-write process or many read-only processes. v1's launcher kept a read-write connection open while starting FastAPI and Streamlit, so both failed to open the file.

#### Decision
The preflight is the only read-write phase and closes its connection before spawning servers, which attach with `DUCKDB_READ_ONLY=true`. Schema changes are applied in place with `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, and older lakehouse files are re-seeded automatically.

### ADR-008 (v2): Credential vault and fail-fast production settings

#### Context
v1 issued a token for any tenant with any secret, accepted a client-chosen role, and shipped its signing key in source control.

#### Decision
Per-tenant scrypt hashes in `TENANT_CREDENTIALS`, server-assigned roles, no default signing key, deny-listing of the published v1 key, and settings validation that refuses to start in production without secrets. Development uses an ephemeral key and documented local demo secrets.

### ADR-009 (v2): Robust anti-poisoning statistics

#### Context
A mean/standard-deviation z-score computed over a batch that contains the outliers is vulnerable to masking: enough poisoned events inflate σ until none of them exceed the threshold.

#### Decision
Use the median and `1.4826·MAD` (50% breakdown point) with the same `|z| > 5` tolerance, record a reason per quarantined event, and fail closed when more than 20% of a tenant's batch is quarantined.
