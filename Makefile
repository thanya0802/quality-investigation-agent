PY ?= python3
.PHONY: real backtest data test eval gate demo api lint clean nl2sql infra load-bq image deploy smoke
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

# ---------------- Milestone 2: GCP ----------------
PROJECT ?= $(shell gcloud config get-value project 2>/dev/null)
REGION ?= us-central1
IMAGE = $(REGION)-docker.pkg.dev/$(PROJECT)/qia/qia:v1
nl2sql:
	$(PY) evals/run_nl2sql.py
infra:
	cd infra && terraform init -upgrade && terraform apply -var project_id=$(PROJECT)
load-bq:
	GCP_PROJECT=$(PROJECT) $(PY) data/load_bigquery.py
image:
	gcloud builds submit --tag $(IMAGE) --region $(REGION) \
	  --service-account projects/$(PROJECT)/serviceAccounts/qia-build@$(PROJECT).iam.gserviceaccount.com \
	  --default-buckets-behavior REGIONAL_USER_OWNED_BUCKET
deploy:
	cd infra && terraform apply -var project_id=$(PROJECT) -var deploy_service=true
smoke:
	@echo "In another terminal: gcloud run services proxy qia --region $(REGION) --port 8081"
	curl -s localhost:8081/v1/health; echo
	curl -s -X POST localhost:8081/v1/investigations -H 'Content-Type: application/json' \
	  -d '{"question": "Investigate Bronco Sport engine complaints"}' | $(PY) -m json.tool | head -40
