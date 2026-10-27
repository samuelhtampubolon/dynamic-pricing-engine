"""Hardening tests: credential verification, JWT claim validation, input abuse and transport headers."""

import hashlib
import secrets
import time

import jwt
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src import config
from src.config import Settings, settings
from src.security import DEMO_TENANT_SECRETS, create_access_token

client = TestClient(app)

QUOTE = {"tenant_id": "tenant_alpha", "sku_id": "SKU-GPU-H100", "inventory_level": 300}


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "sub": "attacker", "tenant_id": "tenant_beta", "role": "pricing_analyst",
        "iat": now, "nbf": now, "exp": now + 600,
        "iss": settings.JWT_ISSUER, "aud": settings.JWT_AUDIENCE, "jti": "x" * 32,
    }
    claims.update(overrides)
    return claims


def test_token_requires_valid_client_secret():
    """API2: a tenant token can no longer be minted with an arbitrary secret."""
    res = client.post("/api/v1/auth/token", json={"tenant_id": "tenant_beta", "client_secret": "anything-goes-here"})
    assert res.status_code == 401
    assert res.json()["detail"] == "Invalid tenant credentials."


def test_token_unknown_tenant_is_indistinguishable():
    """Unknown tenants get the same response as a wrong secret (no enumeration)."""
    res = client.post("/api/v1/auth/token", json={"tenant_id": "tenant_ghost", "client_secret": "whatever-secret-1"})
    assert res.status_code == 401
    assert res.json()["detail"] == "Invalid tenant credentials."


def test_token_role_is_server_assigned():
    """API3/API5: clients cannot self-assign a privileged role."""
    res = client.post("/api/v1/auth/token", json={
        "tenant_id": "tenant_alpha", "client_secret": DEMO_TENANT_SECRETS["tenant_alpha"], "role": "revenue_admin",
    })
    assert res.status_code == 422
    ok = client.post("/api/v1/auth/token", json={
        "tenant_id": "tenant_alpha", "client_secret": DEMO_TENANT_SECRETS["tenant_alpha"],
    })
    assert ok.status_code == 200 and ok.json()["role"] == "pricing_analyst"


def test_forged_token_with_foreign_key_rejected():
    """A token signed with any key other than the server's (e.g. a leaked v1 key) is rejected."""
    forged = jwt.encode(_claims(), secrets.token_hex(32), algorithm="HS256")
    res = client.get("/api/v1/economics/cohorts?tenant_id=tenant_beta", headers=_auth(forged))
    assert res.status_code == 401


def test_compromised_secret_refused_by_settings(monkeypatch):
    """Keys on the compromised list (stored only as SHA-256 digests) are refused at startup."""
    burned = secrets.token_hex(32)
    monkeypatch.setattr(config, "_COMPROMISED_JWT_SECRET_SHA256",
                        frozenset({hashlib.sha256(burned.encode()).hexdigest()}))
    with pytest.raises(ValueError):
        Settings(JWT_SECRET_KEY=burned)
    assert len(config._COMPROMISED_JWT_SECRET_SHA256) == 1


def test_production_requires_secrets():
    with pytest.raises(ValueError):
        Settings(APP_ENV="production")
    with pytest.raises(ValueError):
        Settings(APP_ENV="production", JWT_SECRET_KEY="x" * 48)  # credentials still missing


def test_alg_none_token_rejected():
    unsigned = jwt.encode(_claims(tenant_id="tenant_alpha"), key=None, algorithm="none")
    res = client.post("/api/v1/pricing/quote", json=QUOTE, headers=_auth(unsigned))
    assert res.status_code == 401


@pytest.mark.parametrize("override", [
    {"aud": "some-other-api"},
    {"iss": "evil-issuer"},
    {"exp": int(time.time()) - 3600},
    {"nbf": int(time.time()) + 3600},
    {"role": "superuser"},
])
def test_token_claims_are_validated(override):
    token = jwt.encode(_claims(tenant_id="tenant_alpha", **override), settings.jwt_secret, algorithm="HS256")
    res = client.post("/api/v1/pricing/quote", json=QUOTE, headers=_auth(token))
    assert res.status_code in (401, 403)


def test_auth_error_does_not_leak_parser_details():
    res = client.post("/api/v1/pricing/quote", json=QUOTE, headers=_auth("abc.def.ghi"))
    assert res.status_code == 401
    assert res.json()["detail"] == "Invalid authentication token."


def test_bola_error_does_not_echo_tenant_ids():
    res = client.post("/api/v1/pricing/quote", json={**QUOTE, "tenant_id": "tenant_beta"},
                      headers=_auth(create_access_token("tenant_alpha")))
    assert res.status_code == 403
    assert "tenant_beta" not in res.text and "tenant_alpha" not in res.text


def test_cross_tenant_sku_is_not_found_inside_own_scope():
    """A tenant naming another tenant's SKU inside its own scope gets a 404, never the data."""
    res = client.post("/api/v1/pricing/quote",
                      json={"tenant_id": "tenant_alpha", "sku_id": "SKU-CNC-SPINDLE", "inventory_level": 100},
                      headers=_auth(create_access_token("tenant_alpha")))
    assert res.status_code == 404


@pytest.mark.parametrize("body", [
    '{"tenant_id":"tenant_alpha","sku_id":"SKU-GPU-H100","inventory_level":300,"competitor_price":NaN}',
    '{"tenant_id":"tenant_alpha","sku_id":"SKU-GPU-H100","inventory_level":300,"competitor_price":Infinity}',
    '{"tenant_id":"tenant_alpha","sku_id":"SKU-GPU-H100","inventory_level":300,"demand_surge_factor":NaN}',
])
def test_nan_and_inf_rejected_on_quote(body):
    res = client.post("/api/v1/pricing/quote", content=body,
                      headers={**_auth(create_access_token("tenant_alpha")), "content-type": "application/json"})
    assert res.status_code == 422


def test_nan_and_inf_rejected_in_bandit_arms():
    body = ('{"tenant_id":"tenant_alpha","sku_id":"SKU-GPU-H100","horizon":50,'
            '"candidate_prices":[NaN, 1e308]}')
    res = client.post("/api/v1/simulation/bandit", content=body,
                      headers={**_auth(create_access_token("tenant_alpha")), "content-type": "application/json"})
    assert res.status_code == 422


@pytest.mark.parametrize("sku_id", ["x' OR '1'='1", "SKU-GPU-H100; DROP TABLE sku_catalog;--", "../../etc/passwd"])
def test_injection_payloads_rejected_by_identifier_pattern(sku_id):
    res = client.post("/api/v1/pricing/quote", json={**QUOTE, "sku_id": sku_id},
                      headers=_auth(create_access_token("tenant_alpha")))
    assert res.status_code == 422


def test_validation_errors_do_not_echo_secrets():
    res = client.post("/api/v1/auth/token", json={"tenant_id": "tenant_alpha", "client_secret": "Zq9#xY"})
    assert res.status_code == 422
    assert "Zq9#xY" not in res.text


def test_request_body_size_limit():
    """API4: oversized payloads are refused before parsing."""
    big = '{"tenant_id":"tenant_alpha","sku_id":"SKU-GPU-H100","inventory_level":1,"pad":"' + "a" * 40_000 + '"}'
    res = client.post("/api/v1/pricing/quote", content=big,
                      headers={**_auth(create_access_token("tenant_alpha")), "content-type": "application/json"})
    assert res.status_code == 413


def test_auth_endpoint_rate_limited():
    """API4: credential stuffing against /auth/token is throttled."""
    codes = [
        client.post("/api/v1/auth/token", json={"tenant_id": "tenant_alpha", "client_secret": f"guess-number-{i:04d}"}).status_code
        for i in range(15)
    ]
    assert 429 in codes
    assert codes[0] == 401


def test_untrusted_host_rejected():
    res = client.get("/healthz", headers={"host": "evil.example.com"})
    assert res.status_code == 400


def test_cors_does_not_allow_arbitrary_origins():
    res = client.options("/api/v1/pricing/quote", headers={
        "Origin": "https://evil.example.com", "Access-Control-Request-Method": "POST",
    })
    assert res.headers.get("access-control-allow-origin") not in ("*", "https://evil.example.com")


def test_api_responses_not_cacheable_and_hardened():
    res = client.post("/api/v1/pricing/quote", json=QUOTE, headers=_auth(create_access_token("tenant_alpha")))
    assert res.status_code == 200
    assert res.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in res.headers["Content-Security-Policy"]
    assert res.headers["Referrer-Policy"] == "no-referrer"
    assert res.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert "server" not in res.headers


def test_catalog_and_elasticity_are_tenant_scoped():
    token = create_access_token("tenant_beta")
    cat = client.get("/api/v1/catalog", headers=_auth(token))
    assert cat.status_code == 200
    assert {s["tenant_id"] for s in cat.json()["skus"]} == {"tenant_beta"}
    other = client.get("/api/v1/pricing/elasticity?sku_id=SKU-GPU-H100", headers=_auth(token))
    assert other.status_code == 404
    own = client.get("/api/v1/pricing/elasticity?sku_id=SKU-CNC-SPINDLE", headers=_auth(token))
    assert own.status_code == 200 and own.json()["elasticity_epsilon"] < -1.0
