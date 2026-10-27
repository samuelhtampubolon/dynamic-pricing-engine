.PHONY: install check test report seed run run-api run-ui audit clean help

PYTHON ?= python

help:
	@echo "Real-Time Dynamic Pricing & Unit Economics Engine - Make Commands"
	@echo "------------------------------------------------------------------"
	@echo "make install    Install all project dependencies"
	@echo "make check      Preflight: seed/migrate lakehouse, P95 latency, guardrail fuzzing, ports"
	@echo "make seed       Re-seed 12 months of synthetic multi-SKU data"
	@echo "make test       Run the pytest suite"
	@echo "make report     Run pytest and refresh docs/assets/data/test-report.json"
	@echo "make run        Launch FastAPI and Streamlit concurrently (read-only lakehouse)"
	@echo "make run-api    Launch FastAPI REST server on port 8000"
	@echo "make run-ui     Launch Streamlit Cockpit on port 8501"
	@echo "make audit      Scan dependencies for known vulnerabilities (pip-audit)"
	@echo "make clean      Remove cached bytecode and temporary files"

install:
	$(PYTHON) -m pip install -r requirements.txt

check:
	$(PYTHON) run_local.py --check

seed:
	$(PYTHON) -m src.seed_market_data

test:
	$(PYTHON) -m pytest -v

report:
	$(PYTHON) scripts/build_test_report.py

run:
	$(PYTHON) run_local.py

run-api: check
	DUCKDB_READ_ONLY=true $(PYTHON) -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --no-server-header

run-ui: check
	DUCKDB_READ_ONLY=true $(PYTHON) -m streamlit run src/ui/dashboard.py --server.port 8501 --server.address 127.0.0.1 --theme.base dark

audit:
	$(PYTHON) -m pip install --quiet pip-audit && $(PYTHON) -m pip_audit -r requirements.txt

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
