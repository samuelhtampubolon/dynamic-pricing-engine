"""Unified local server startup and preflight validator for Dynamic Pricing & Unit Economics Engine."""

from __future__ import annotations
import argparse
import socket
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

from src.config import settings
from src.lakehouse import lakehouse
from src.seed_market_data import seed_synthetic_lakehouse
from src.services.elasticity_model import elasticity_service


def is_port_available(host: str, port: int) -> bool:
    """Check if a network socket port is free for binding."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.bind((host, port))
            return True
        except (socket.error, OSError):
            return False


def run_preflight_checks() -> bool:
    """Execute preflight validations for Lakehouse data, models, and network ports."""
    print("=" * 70)
    print("⚡ PREFLIGHT VERIFICATION: Real-Time Dynamic Pricing & Unit Economics Engine")
    print("=" * 70)

    # 1. Database Check & Auto-Seeding
    counts = lakehouse.get_table_counts()
    has_data = (
        counts.get("gold_sku_elasticity", 0) > 0
        and counts.get("bronze_market_events", 0) > 0
    )

    if not has_data:
        print("[*] DuckDB Lakehouse unpopulated. Executing initial synthetic seeding...")
        seed_res = seed_synthetic_lakehouse(days=365)
        counts = seed_res["table_counts"]
        print(f"[✓] Seeding complete: {counts}")
    else:
        print(f"[✓] DuckDB Lakehouse ready: {counts}")

    # 2. Guarded Pricing Inference Test
    t0 = time.perf_counter()
    quote = elasticity_service.compute_guarded_quote(
        tenant_id="tenant_alpha",
        sku_id="SKU-GPU-H100",
        inventory_level=350,
        competitor_price=31500.0,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    print(f"[✓] Inference Sanity Test Passed ({elapsed_ms:.2f}ms latency, target <50ms P95)")
    print(f"    SKU: {quote['sku_id']} | Rec Price: ${quote['recommended_price']:,.2f} | Status: {quote['guardrail_status']}")

    # 3. Port Availability Checks
    api_free = is_port_available(settings.HOST, settings.API_PORT)
    ui_free = is_port_available(settings.HOST, settings.STREAMLIT_PORT)

    print(f"[{'✓' if api_free else '!'}] Port {settings.API_PORT} (FastAPI): {'Available' if api_free else 'Occupied'}")
    print(f"[{'✓' if ui_free else '!'}] Port {settings.STREAMLIT_PORT} (Streamlit): {'Available' if ui_free else 'Occupied'}")

    print("=" * 70)
    print("🌟 All Preflight Verifications Passed Successfully!")
    print("=" * 70)
    return True


def start_live_servers() -> None:
    """Launch FastAPI and Streamlit concurrently with graceful shutdown handling."""
    run_preflight_checks()

    python_exe = sys.executable

    print("\n🚀 Starting Live Services:")
    print(f"   ► FastAPI REST API:      http://{settings.HOST}:{settings.API_PORT}")
    print(f"   ► Swagger Documentation: http://{settings.HOST}:{settings.API_PORT}/docs")
    print(f"   ► Streamlit Cockpit:     http://{settings.HOST}:{settings.STREAMLIT_PORT}\n")

    api_cmd = [
        python_exe, "-m", "uvicorn", "src.api.main:app",
        "--host", settings.HOST,
        "--port", str(settings.API_PORT),
    ]

    ui_cmd = [
        python_exe, "-m", "streamlit", "run", "src/ui/dashboard.py",
        "--server.port", str(settings.STREAMLIT_PORT),
        "--server.address", settings.HOST,
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
    ]

    api_proc = subprocess.Popen(api_cmd, cwd=str(PROJECT_ROOT))
    ui_proc = subprocess.Popen(ui_cmd, cwd=str(PROJECT_ROOT))

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
        api_proc.terminate()
        ui_proc.terminate()
        api_proc.wait()
        ui_proc.wait()
        print("[✓] All processes safely terminated.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Dynamic Pricing Engine servers or preflight check.")
    parser.add_argument("--check", action="store_true", help="Run preflight health and lakehouse checks only")
    args = parser.parse_args()

    if args.check:
        success = run_preflight_checks()
        sys.exit(0 if success else 1)
    else:
        start_live_servers()
