"""Comprehensive pytest suite for OWASP API Security Controls and Guardrails."""

import pytest
from fastapi.testclient import TestClient
from src.api.main import app
from src.security import create_access_token

client = TestClient(app)


def test_healthz_and_readyz_probes():
    """Verify health and readiness probes."""
    h_res = client.get("/healthz")
    assert h_res.status_code == 200
    assert h_res.json()["status"] == "healthy"

    r_res = client.get("/readyz")
    assert r_res.status_code == 200
    assert r_res.json()["status"] == "ready"
    assert r_res.json()["lakehouse_ready"] is True


def test_jwt_token_issuance_and_access():
    """Verify tenant token generation and authenticated quote request."""
    t_res = client.post("/api/v1/auth/token", json={
        "tenant_id": "tenant_alpha",
        "client_secret": "secure_secret_pass",
    })
    assert t_res.status_code == 200
    token = t_res.json()["access_token"]
    assert token is not None

    # Authenticated quote call
    q_res = client.post(
        "/api/v1/pricing/quote",
        json={
            "tenant_id": "tenant_alpha",
            "sku_id": "SKU-GPU-H100",
            "inventory_level": 300,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert q_res.status_code == 200
    data = q_res.json()
    assert data["sku_id"] == "SKU-GPU-H100"
    assert data["recommended_price"] >= data["margin_floor_price"]


def test_owasp_api1_bola_cross_tenant_isolation():
    """OWASP API1: Tenant A token MUST be forbidden from accessing Tenant B resources."""
    # Issue token for tenant_alpha
    token_alpha = create_access_token(tenant_id="tenant_alpha")

    # Attempt to request quote for tenant_beta using tenant_alpha token
    q_res = client.post(
        "/api/v1/pricing/quote",
        json={
            "tenant_id": "tenant_beta",  # CROSS-TENANT ATTEMPT
            "sku_id": "SKU-CNC-SPINDLE",
            "inventory_level": 200,
        },
        headers={"Authorization": f"Bearer {token_alpha}"},
    )
    assert q_res.status_code == 403, "BOLA breach: Tenant Alpha accessed Tenant Beta pricing!"
    assert "BOLA Violation" in q_res.json()["detail"]

    # Attempt to read cohorts for tenant_beta using tenant_alpha token
    c_res = client.get(
        "/api/v1/economics/cohorts?tenant_id=tenant_beta",
        headers={"Authorization": f"Bearer {token_alpha}"},
    )
    assert c_res.status_code == 403, "BOLA breach: Tenant Alpha accessed Tenant Beta cohorts!"


def test_owasp_api2_broken_authentication():
    """OWASP API2: Requests without token or with forged token are rejected."""
    # No auth header
    q_no_auth = client.post(
        "/api/v1/pricing/quote",
        json={
            "tenant_id": "tenant_alpha",
            "sku_id": "SKU-GPU-H100",
            "inventory_level": 300,
        },
    )
    assert q_no_auth.status_code in [401, 403]

    # Forged token
    q_forged = client.post(
        "/api/v1/pricing/quote",
        json={
            "tenant_id": "tenant_alpha",
            "sku_id": "SKU-GPU-H100",
            "inventory_level": 300,
        },
        headers={"Authorization": "Bearer forged.invalid.token"},
    )
    assert q_forged.status_code == 401


def test_owasp_api3_mass_assignment_extra_fields():
    """OWASP API3: Payload with unapproved extra fields must be rejected (extra='forbid')."""
    token_alpha = create_access_token(tenant_id="tenant_alpha")

    q_res = client.post(
        "/api/v1/pricing/quote",
        json={
            "tenant_id": "tenant_alpha",
            "sku_id": "SKU-GPU-H100",
            "inventory_level": 300,
            "admin_override_margin": 0.0,  # UNAPPROVED INJECTED ATTRIBUTE
            "bypass_guardrails": True,
        },
        headers={"Authorization": f"Bearer {token_alpha}"},
    )
    assert q_res.status_code == 422, "Mass assignment vulnerability: Extra fields accepted!"


def test_owasp_api3_numeric_bounds_validation():
    """OWASP API3: Negative inventory or competitor price must be rejected."""
    token_alpha = create_access_token(tenant_id="tenant_alpha")

    # Negative inventory
    q_res_inv = client.post(
        "/api/v1/pricing/quote",
        json={
            "tenant_id": "tenant_alpha",
            "sku_id": "SKU-GPU-H100",
            "inventory_level": -50,
        },
        headers={"Authorization": f"Bearer {token_alpha}"},
    )
    assert q_res_inv.status_code == 422

    # Negative competitor price
    q_res_comp = client.post(
        "/api/v1/pricing/quote",
        json={
            "tenant_id": "tenant_alpha",
            "sku_id": "SKU-GPU-H100",
            "inventory_level": 100,
            "competitor_price": -500.0,
        },
        headers={"Authorization": f"Bearer {token_alpha}"},
    )
    assert q_res_comp.status_code == 422


def test_owasp_api4_resource_consumption_cap():
    """OWASP API4: Simulation horizon > 5000 must be rejected."""
    token_alpha = create_access_token(tenant_id="tenant_alpha")

    sim_res = client.post(
        "/api/v1/simulation/bandit",
        json={
            "tenant_id": "tenant_alpha",
            "sku_id": "SKU-GPU-H100",
            "horizon": 10000,  # EXCEEDS MAX CAP (5000)
            "algorithm": "thompson_sampling",
        },
        headers={"Authorization": f"Bearer {token_alpha}"},
    )
    assert sim_res.status_code == 422, "Resource exhaustion vulnerability: Horizon > 5000 accepted!"


def test_owasp_security_headers_injection():
    """Verify security headers middleware injects defense-in-depth HTTP headers."""
    res = client.get("/healthz")
    assert res.headers.get("X-Content-Type-Options") == "nosniff"
    assert res.headers.get("X-Frame-Options") == "DENY"
    assert "default-src 'self'" in res.headers.get("Content-Security-Policy", "")
    assert "max-age=31536000" in res.headers.get("Strict-Transport-Security", "")
