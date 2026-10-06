terraform {
  required_version = ">= 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.5"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone

  # With user credentials (gcloud ADC) some APIs, e.g. billing budgets, need an explicit quota
  # project; bill all API quota to this project.
  user_project_override = true
  billing_project       = var.project_id

  default_labels = {
    project    = "llm-serving"
    managed-by = "terraform"
  }
}
