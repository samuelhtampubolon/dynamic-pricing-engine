"""Unified local server startup and preflight validator for Dynamic Pricing & Unit Economics Engine."""

from __future__ import annotations

import argparse
import os
import random
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

# Ensure UTF-8 output encoding on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import settings  # noqa: E402
from src.lakehouse import lakehouse  # noqa: E402
from src.seed_market_data import seed_synthetic_lakehouse  # noqa: E402
from src.services.elasticity_model import elasticity_service  # noqa: E402

LATENCY_SAMPLES = 200
GUARDRAIL_FUZZ_CASES = 1000


def is_port_available(host: str, port: int) -> bool:
    """Check if a network socket port is free for binding."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def run_preflight_checks(require_free_ports: bool = False) -> bool:
    """Execute preflight validations for Lakehouse data, models, guardrails and network ports."""
    ok = True
    print("=" * 72)
    print("⚡ PREFLIGHT VERIFICATION: Real-Time Dynamic Pricing & Unit Economics Engine")
    print("=" * 72)

    # 1. Lakehouse: schema migration + auto-seeding (the only read-write phase)
    lakehouse.initialize_schema()
    if not lakehouse.is_current_schema_populated():
        print("[*] Lakehouse empty or on an older schema. Seeding 12 months of synthetic data...")
        seed_res = seed_synthetic_lakehouse(days=365)
        for t_id, gate in seed_res["quality_gates"].items():
            print(f"    gate {t_id}: {gate['status']} | quarantined {gate['rejected_poison_count']} "
                  f"of {gate['total_bronze']} events {gate['rejection_reasons']}")
    counts = lakehouse.get_table_counts()
    print(f"[✓] DuckDB Medallion Lakehouse ready: {counts}")

    # 2. Inference latency (P95 target < 50ms)
    skus = [(t, s["sku_id"]) for t in ("tenant_alpha", "tenant_beta") for s in lakehouse.get_sku_catalog(t)]
    timings = []
    for i in range(LATENCY_SAMPLES):
        tenant_id, sku_id = skus[i % len(skus)]
        t0 = time.perf_counter()
        elasticity_service.compute_guarded_quote(tenant_id, sku_id, inventory_level=350, competitor_price=None)
        timings.append((time.perf_counter() - t0) * 1000.0)
    p95 = statistics.quantiles(timings, n=20)[-1]
    latency_ok = p95 < 50.0
    ok &= latency_ok
    print(f"[{'✓' if latency_ok else '✗'}] Quote latency P95 = {p95:.2f}ms over {LATENCY_SAMPLES} calls (target < 50ms)")

    # 3. Guardrail invariant fuzzing: floor <= price <= ceiling for every input
    rng = random.Random(2026)
    violations = 0
    for _ in range(GUARDRAIL_FUZZ_CASES):
        tenant_id, sku_id = rng.choice(skus)
        q = elasticity_service.compute_guarded_quote(
            tenant_id, sku_id,
            inventory_level=rng.choice([0, 1, rng.randint(0, 1_000_000)]),
            competitor_price=rng.choice([None, 0.01, rng.uniform(1, 1_000_000)]),
            demand_surge_factor=rng.uniform(0.1, 10.0),
        )
        if not (q["margin_floor_price"] <= q["recommended_price"] <= q["max_msrp_ceiling"]):
            violations += 1
    fuzz_ok = violations == 0
    ok &= fuzz_ok
    print(f"[{'✓' if fuzz_ok else '✗'}] Margin-floor / MSRP circuit breaker held on {GUARDRAIL_FUZZ_CASES} fuzzed quotes "
          f"({violations} violations)")

    sample = elasticity_service.compute_guarded_quote("tenant_alpha", "SKU-GPU-H100", 350, 31500.0)
    print(f"    SKU-GPU-H100 → ${sample['recommended_price']:,.2f} | {sample['guardrail_status']} | "
          f"{sample['inventory_regime']} | ε={sample['elasticity_epsilon']}")

    # 4. Security posture
    print(f"[✓] Environment: {settings.ENV} | JWT {settings.JWT_ALGORITHM}, {settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES} min TTL")
    if not settings.TENANT_CREDENTIALS:
        print("[!] TENANT_CREDENTIALS not set: local demo credentials are active (never use in production).")

    # 5. Port availability
    for name, port in (("FastAPI", settings.API_PORT), ("Streamlit", settings.STREAMLIT_PORT)):
        free = is_port_available(settings.HOST, port)
        if require_free_ports and not free:
            ok = False
        print(f"[{'✓' if free else '!'}] Port {port} ({name}): {'Available' if free else 'Occupied'}")

    # Release the read-write lock so the read-only serving processes can attach.
    lakehouse.close()

    print("=" * 72)
    print("🌟 All Preflight Verifications Passed Successfully!" if ok else "✗ Preflight verification FAILED.")
    print("=" * 72)
    return ok


def start_live_servers() -> None:
    """Launch FastAPI and Streamlit concurrently with graceful shutdown handling."""
    if not run_preflight_checks(require_free_ports=True):
        sys.exit(1)

    python_exe = sys.executable
    child_env = {**os.environ, "DUCKDB_READ_ONLY": "true"}

    print("\n🚀 Starting Live Services:")
    print(f"   ► FastAPI REST API:      http://{settings.HOST}:{settings.API_PORT}")
    print(f"   ► Swagger Documentation: http://{settings.HOST}:{settings.API_PORT}/docs")
    print(f"   ► Streamlit Cockpit:     http://{settings.HOST}:{settings.STREAMLIT_PORT}\n")

    api_cmd = [
        python_exe, "-m", "uvicorn", "src.api.main:app",
        "--host", settings.HOST,
        "--port", str(settings.API_PORT),
        "--no-server-header",
    ]

    ui_cmd = [
        python_exe, "-m", "streamlit", "run", "src/ui/dashboard.py",
        "--server.port", str(settings.STREAMLIT_PORT),
        "--server.address", settings.HOST,
        "--server.headless", "true",
        "--server.enableXsrfProtection", "true",
        "--browser.gatherUsageStats", "false",
        "--client.toolbarMode", "minimal",
        "--theme.base", "dark",
    ]

    api_proc = subprocess.Popen(api_cmd, cwd=str(PROJECT_ROOT), env=child_env)
    ui_proc = subprocess.Popen(ui_cmd, cwd=str(PROJECT_ROOT), env=child_env)

    try:
        while True:
            time.sleep(1)
            if api_proc.poll() is not None:
                print("[!] FastAPI process terminated unexpectedly.")
                break
            if ui_proc.poll() is not None:
                print("[!] Streamlit process terminated unexpectedly.")
                break
    except KeyboardInterrupt:
        print("\n[*] Shutting down servers gracefully...")
    finally:
        for proc in (api_proc, ui_proc):
            proc.terminate()
        for proc in (api_proc, ui_proc):
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        print("[✓] All processes safely terminated.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Dynamic Pricing Engine servers or preflight check.")
    parser.add_argument("--check", action="store_true", help="Run preflight health and lakehouse checks only")
    args = parser.parse_args()

    if args.check:
        sys.exit(0 if run_preflight_checks() else 1)
    else:
        start_live_servers()
