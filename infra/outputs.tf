output "image" { value = local.image }
output "agent_service_account" { value = google_service_account.agent.email }
output "build_service_account" { value = google_service_account.build.email }
output "service_url" { value = var.deploy_service ? google_cloud_run_v2_service.qia[0].uri : "not deployed yet" }
