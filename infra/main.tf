# Quality Investigation Agent: all GCP resources as code. Every service used here has a free tier.
terraform {
  required_version = ">= 1.6"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 6.0" }
  }
}

provider "google" {
  project               = var.project_id
  region                = var.region
  user_project_override = true             # needed for the billing-budget API
  billing_project       = var.project_id
}

locals {
  apis = ["bigquery.googleapis.com", "run.googleapis.com", "artifactregistry.googleapis.com",
          "firestore.googleapis.com", "secretmanager.googleapis.com", "iam.googleapis.com",
          "cloudbuild.googleapis.com", "billingbudgets.googleapis.com"]
  image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.qia.repository_id}/qia:${var.image_tag}"
}

resource "google_project_service" "apis" {
  for_each           = toset(local.apis)
  service            = each.value
  disable_on_destroy = false
}

# ---------------- BigQuery: analytics data (agent has read-only access) ----------------
resource "google_bigquery_dataset" "quality" {
  dataset_id                 = "quality"
  location                   = "US"
  delete_contents_on_destroy = true
  depends_on                 = [google_project_service.apis]
}

resource "google_bigquery_table" "complaints" {
  dataset_id          = google_bigquery_dataset.quality.dataset_id
  table_id            = "complaints"
  deletion_protection = false
  # MONTH partitions (~80 for 2020-2026) rather than DAY (~2,500 tiny ones); clustering on the
  # columns every agent query filters by, so BigQuery scans only the blocks it needs.
  time_partitioning {
    type  = "MONTH"
    field = "received_dt"
  }
  clustering = ["model", "component"]
  schema = jsonencode([
    { name = "complaint_id", type = "STRING", mode = "REQUIRED" },
    { name = "make", type = "STRING" }, { name = "model", type = "STRING" },
    { name = "model_year", type = "INTEGER" }, { name = "component", type = "STRING" },
    { name = "crash", type = "INTEGER" }, { name = "fire", type = "INTEGER" }, { name = "injured", type = "INTEGER" },
    { name = "fail_date", type = "STRING" }, { name = "received_date", type = "STRING" },
    { name = "week_start", type = "STRING" }, { name = "state", type = "STRING" },
    { name = "description", type = "STRING" }, { name = "received_dt", type = "DATE" },
  ])
}

resource "google_bigquery_table" "recalls" {
  dataset_id          = google_bigquery_dataset.quality.dataset_id
  table_id            = "recalls"
  deletion_protection = false
  schema = jsonencode([
    { name = "recall_id", type = "STRING", mode = "REQUIRED" },
    { name = "make", type = "STRING" }, { name = "model", type = "STRING" },
    { name = "model_years", type = "STRING" }, { name = "component", type = "STRING" },
    { name = "report_date", type = "STRING" }, { name = "defect_summary", type = "STRING" },
    { name = "remedy", type = "STRING" },
  ])
}

# ---------------- Firestore: investigation memory + audit log ----------------
resource "google_firestore_database" "default" {
  name        = "(default)"          # the free tier applies to the default database
  location_id = var.firestore_location
  type        = "FIRESTORE_NATIVE"
  depends_on  = [google_project_service.apis]
}

# ---------------- Secret: Gemini API key (value added with gcloud, never stored in Terraform state) ----------------
resource "google_secret_manager_secret" "gemini" {
  secret_id = "gemini-api-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

# ---------------- Container registry ----------------
resource "google_artifact_registry_repository" "qia" {
  repository_id = "qia"
  location      = var.region
  format        = "DOCKER"
  cleanup_policies {                 # stay inside the 0.5 GB free storage
    id     = "keep-latest-2"
    action = "KEEP"
    most_recent_versions { keep_count = 2 }
  }
  depends_on = [google_project_service.apis]
}

# ---------------- Least-privilege service accounts ----------------
resource "google_service_account" "agent" {
  account_id   = "qia-agent"
  display_name = "Quality Investigation Agent (runtime)"
  depends_on   = [google_project_service.apis]   # IAM API must be on (and propagated) first
}

resource "google_bigquery_dataset_iam_member" "agent_reads_data" {     # read-only: no dataEditor
  dataset_id = google_bigquery_dataset.quality.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.agent.email}"
}

resource "google_project_iam_member" "agent_runs_queries" {
  for_each = toset(["roles/bigquery.jobUser", "roles/datastore.user", "roles/logging.logWriter"])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.agent.email}"
}

resource "google_secret_manager_secret_iam_member" "agent_reads_key" {
  secret_id = google_secret_manager_secret.gemini.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.agent.email}"
}

resource "google_service_account" "build" {
  account_id   = "qia-build"
  display_name = "Quality Investigation Agent (image builds)"
  depends_on   = [google_project_service.apis]
}

resource "google_project_iam_member" "build" {
  for_each = toset(["roles/logging.logWriter", "roles/storage.objectViewer"])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.build.email}"
}

resource "google_artifact_registry_repository_iam_member" "build_pushes" {
  repository = google_artifact_registry_repository.qia.name
  location   = var.region
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.build.email}"
}

# ---------------- Cloud Run (created on the second apply, once the image and secret exist) ----------------
resource "google_cloud_run_v2_service" "qia" {
  count               = var.deploy_service ? 1 : 0
  name                = "qia"
  location            = var.region
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"
  template {
    service_account                  = google_service_account.agent.email
    max_instance_request_concurrency = 10
    scaling {
      min_instance_count = 0         # scale to zero: no idle cost
      max_instance_count = 2
    }
    containers {
      image = local.image
      resources {
        limits            = { cpu = "1", memory = "1Gi" }
        cpu_idle          = true     # CPU only billed while handling requests
        startup_cpu_boost = true
      }
      env {
        name  = "WAREHOUSE"
        value = "bigquery"
      }
      env {
        name  = "STATE_STORE"
        value = "firestore"
      }
      env {
        name  = "GCP_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GEMINI_MODEL"
        value = var.gemini_model
      }
      env {
        name = "GEMINI_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.gemini.secret_id
            version = "latest"
          }
        }
      }
    }
  }
  depends_on = [google_secret_manager_secret_iam_member.agent_reads_key, google_project_iam_member.agent_runs_queries]
}
# No allUsers invoker binding: the service is private. Call it with `gcloud run services proxy`.

# ---------------- Budget alert (optional; alerts only, it does not stop spending) ----------------
resource "google_billing_budget" "qia" {
  count           = var.billing_account == "" ? 0 : 1
  billing_account = var.billing_account
  display_name    = "qia-budget"
  budget_filter {
    projects = ["projects/${data.google_project.this.number}"]
  }
  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.budget_usd)
    }
  }
  threshold_rules { threshold_percent = 0.5 }
  threshold_rules { threshold_percent = 0.9 }
  threshold_rules { threshold_percent = 1.0 }
}

data "google_project" "this" {}
