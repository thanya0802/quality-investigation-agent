PY ?= python3
.PHONY: real backtest data test eval gate demo api lint clean
MODELS ?= F-150,ESCAPE,EXPLORER,BRONCO SPORT,MUSTANG MACH-E
real:             ## REAL NHTSA data: complaints from data/raw + recalls from NHTSA's API
	$(PY) data/load_nhtsa.py --make FORD --models "$(MODELS)" --years 2020-2026
	$(PY) data/fetch_recalls.py
backtest:         ## did complaint spikes precede real recalls?
	$(PY) evals/backtest.py
data:             ## synthetic fixture (tests and CI only)
	$(PY) data/generate.py
test:
	$(PY) -m pytest -q
eval:
	$(PY) evals/run_evals.py
gate:
	$(PY) evals/run_evals.py --gate
demo:
	$(PY) -m qia.cli ask "Are there any emerging issues?"
	$(PY) -m qia.cli ask "Investigate the Vesta brake complaints"
api:
	uvicorn api.main:app --reload --port 8000
lint:
	ruff check .
clean:
	rm -rf data/*.db evals/results
