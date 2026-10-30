# Deploying to GCP

Prerequisites: `gcloud` and `terraform` installed, a GCP project with billing enabled (free trial is fine).

## 1. Authenticate
```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
```

## 2. Create the infrastructure (first apply: no Cloud Run service yet)
A new project ships with the Cloud Resource Manager API disabled, and Terraform needs it to enable
the other APIs, so turn on the two bootstrap APIs once by hand:
```bash
make bootstrap
make infra
```
If service-account creation fails with "IAM API has not been used", the API is still propagating;
wait a minute and run `make infra` again (Terraform only creates what's missing).
Creates: APIs, BigQuery dataset and tables, Firestore, Secret Manager secret, Artifact Registry,
service accounts with least privilege. Type `yes` when Terraform shows the plan.

## 3. Add the Gemini key as a secret version (never in Terraform state or git)
```bash
printf "%s" "$GEMINI_API_KEY" | gcloud secrets versions add gemini-api-key --data-file=-
```

## 4. Load the real data into BigQuery (after `make real` locally)
```bash
make load-bq
```

## 5. Build the image with Cloud Build
```bash
make image
```
Builds run as the `qia-build` service account and log to Cloud Logging only (see cloudbuild.yaml).

## 6. Deploy the Cloud Run service
```bash
make deploy
```

## 7. Smoke test (the service is private; the proxy authenticates as you)
```bash
gcloud run services proxy qia --region us-central1 --port 8081
```
Leave it running; in a second terminal: `make smoke`. The first request includes a cold start.
Service logs: `gcloud run services logs read qia --region us-central1 --limit 30`.

## Cost
BigQuery (1 TiB queries and 10 GiB storage free per month), Cloud Run (2M requests free),
Firestore (free daily quotas), Artifact Registry (0.5 GB free; cleanup policy keeps 2 images),
Secret Manager and Cloud Build free tiers. Every query has a `maximum_bytes_billed` cap.
To remove everything: `cd infra && terraform destroy -var project_id=YOUR_PROJECT_ID`.
