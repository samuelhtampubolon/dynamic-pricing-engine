# Security

## Reporting a vulnerability

Please open a private [GitHub security advisory](https://github.com/samuelhtampubolon/dynamic-pricing-engine/security/advisories/new) instead of a public issue.

## Threat model

Multi-tenant pricing data (COGS, margin floors, elasticities, cohort economics) is commercially sensitive. The main threats are cross-tenant data access, forged or replayed tokens, payload manipulation of pricing inputs, resource exhaustion, and poisoning of the demand history that drives prices.

## Controls (OWASP API Security Top 10, 2023)

| Risk | Control | Where | Test |
| :--- | :--- | :--- | :--- |
| API1 BOLA | Tenant taken from the verified JWT; any request naming another tenant gets 403 (no tenant IDs echoed); every query is `tenant_id = ?` parameterised | `security.enforce_bola_isolation`, `routes_pricing.py` | `test_owasp_api1_*`, `test_bola_error_does_not_echo_tenant_ids`, `test_cross_tenant_sku_is_not_found_inside_own_scope` |
| API2 Broken authentication | Per-tenant client secrets stored as scrypt hashes, verified in constant time with a dummy hash for unknown tenants; HS256 key with no default (production refuses to start without one, the v1 key is deny-listed); `alg` pinned; `iss`, `aud`, `exp`, `nbf`, `iat`, `jti` required; 30 min TTL; 10 req/min on `/auth/token` | `credentials.py`, `security.py`, `config.py` | `test_token_*`, `test_forged_token_with_leaked_v1_secret_rejected`, `test_alg_none_token_rejected`, `test_token_claims_are_validated`, `test_auth_endpoint_rate_limited` |
| API3 Property-level authorization | `extra='forbid'`, `allow_inf_nan=False`, numeric bounds and ID patterns on every model; role is assigned by the server | `schemas.py` | `test_owasp_api3_*`, `test_nan_and_inf_*`, `test_token_role_is_server_assigned` |
| API4 Resource consumption | Horizon ≤ 5,000; 32 KB body cap (also for chunked bodies); slowapi default and per-route limits | `schemas.py`, `security.BodySizeLimitMiddleware` | `test_owasp_api4_resource_consumption_cap`, `test_request_body_size_limit` |
| API5 Function-level authorization | Role claim allow-listed (`pricing_analyst`, `growth_manager`, `revenue_admin`); unknown roles get 403 | `security.get_current_principal` | `test_token_claims_are_validated` |
| API8 Security misconfiguration | CSP, HSTS, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`, Permissions-Policy, COOP, CORP, `Cache-Control: no-store` on `/api`, no `Server` header; CORS allow-list without credentials; TrustedHost; Swagger/ReDoc/OpenAPI disabled in production; validation errors never echo input; 5xx bodies are generic | `security.SecurityHeadersMiddleware`, `api/main.py` | `test_owasp_security_headers_injection`, `test_api_responses_not_cacheable_and_hardened`, `test_cors_*`, `test_untrusted_host_rejected`, `test_validation_errors_do_not_echo_secrets` |
| API9 Inventory management | One versioned surface `/api/v1`; docs off in production | `api/main.py` | — |
| Injection | DuckDB `?` bindings only; table names from a fixed allow-list | `lakehouse.py` | `test_injection_payloads_rejected_by_identifier_pattern` |

## Algorithmic safety

- **Margin-floor / MSRP circuit breaker**: `min(MSRP, max(c(1+m), P))` is applied in `pricing_math.clamp_to_guardrails` and again in `ElasticityModelService.compute_guarded_quote`; NaN fails closed to the floor and an inconsistent SKU (floor > MSRP) is refused with 409. Verified by a 3,000-case fuzz test and 1,000 fuzzed quotes at every preflight.
- **Anti-poisoning gate**: robust z-score `|x − median| / (1.4826·MAD) > 5` on price, competitor price and volume, so a coordinated batch cannot mask itself; if more than 20% of a tenant's events are quarantined the gate fails closed and Silver is not refreshed.

## Deployment hardening

- Local servers bind to `127.0.0.1`; Docker publishes on loopback only.
- Container runs as UID 10001 with a read-only root filesystem, `no-new-privileges` and all capabilities dropped.
- Serving processes attach to the lakehouse read-only.
- The GitHub Pages site loads no third-party origin; its CSP is `default-src 'self'; script-src 'self'`.

## Rotating credentials

1. Generate a new key: `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
2. Hash new tenant secrets: `python -m src.credentials hash-secret`.
3. Update `.env` and restart. Existing tokens are invalidated immediately because they were signed with the old key.
