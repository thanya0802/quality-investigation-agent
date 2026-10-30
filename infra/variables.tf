variable "project_id" { type = string }
variable "region" {
  type    = string
  default = "us-central1"
}
variable "firestore_location" {
  type    = string
  default = "nam5"
}
variable "deploy_service" {
  description = "false on the first apply (infra only); true once the image is built and the secret has a value"
  type        = bool
  default     = false
}
variable "image_tag" {
  type    = string
  default = "v1"
}
variable "gemini_model" {
  type    = string
  default = "gemini-3.5-flash-lite"
}
variable "billing_account" {
  description = "Billing account ID for the budget alert; leave empty to skip"
  type        = string
  default     = ""
}
variable "budget_usd" {
  type    = number
  default = 10
}
