.PHONY: install check test seed run run-api run-ui clean help

PYTHON ?= python

help:
	@echo "Real-Time Dynamic Pricing & Unit Economics Engine - Make Commands"
	@echo "------------------------------------------------------------------"
	@echo "make install    Install all project dependencies"
	@echo "make check      Run preflight health and lakehouse checks"
	@echo "make seed       Populate 12 months of synthetic multi-SKU data"
	@echo "make test       Run pytest test suite (14 automated checks)"
	@echo "make run        Launch FastAPI and Streamlit concurrently"
	@echo "make run-api    Launch FastAPI REST server on port 8000"
	@echo "make run-ui     Launch Streamlit Cockpit on port 8501"
	@echo "make clean      Remove cached bytecode and temporary files"

install:
	$(PYTHON) -m pip install -r requirements.txt

check:
	$(PYTHON) run_local.py --check

seed:
	$(PYTHON) -m src.seed_market_data

test:
	$(PYTHON) -m pytest -v

run:
	$(PYTHON) run_local.py

run-api:
	$(PYTHON) -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --reload

run-ui:
	$(PYTHON) -m streamlit run src/ui/dashboard.py --server.port 8501 --server.address 127.0.0.1

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
