# Dev environment: APIs, VPC, GKE cluster (CPU pool + Spot L4 pool at 0 nodes), Artifact
# Registry, Workload Identity grants and a budget alert.

locals {
  apis = toset([
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "billingbudgets.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "compute.googleapis.com",
    "container.googleapis.com",
    "containerfilesystem.googleapis.com", # GKE image streaming
    "iam.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
  ])

  # Workload Identity Federation for GKE: IAM roles are granted straight to Kubernetes service
  # accounts, no Google service accounts or keys involved.
  wi_principal = "principal://iam.googleapis.com/projects/${data.google_project.this.number}/locations/global/workloadIdentityPools/${var.project_id}.svc.id.goog/subject"
}

# Read after the APIs are enabled: on a fresh project the Cloud Resource Manager API it calls is
# itself one of them.
data "google_project" "this" {
  project_id = var.project_id

  depends_on = [google_project_service.apis]
}

resource "google_project_service" "apis" {
  for_each = local.apis

  project = var.project_id
  service = each.value
  # Never switch an API off on destroy: other things in the project may depend on it.
  disable_on_destroy = false
}

module "network" {
  source = "../../modules/network"

  project_id = var.project_id
  region     = var.region
  name       = var.cluster_name

  depends_on = [google_project_service.apis]
}

module "gke" {
  source = "../../modules/gke"

  project_id          = var.project_id
  cluster_name        = var.cluster_name
  zone                = var.zone
  network_id          = module.network.network_id
  subnetwork_id       = module.network.subnetwork_id
  pods_range_name     = module.network.pods_range_name
  services_range_name = module.network.services_range_name
  cpu_machine_type    = var.cpu_machine_type
  gpu_machine_type    = var.gpu_machine_type
  gpu_type            = var.gpu_type
  gpu_driver_version  = var.gpu_driver_version
  gpu_zones           = coalescelist(var.gpu_zones, [var.zone])
  gpu_max_nodes       = var.gpu_pool_max_nodes

  depends_on = [google_project_service.apis]
}

module "registry" {
  source = "../../modules/registry"

  project_id        = var.project_id
  region            = var.region
  images_repo_id    = var.artifact_registry_repo
  dockerhub_repo_id = var.dockerhub_remote_repo
  reader_members    = ["serviceAccount:${module.gke.node_service_account}"]

  depends_on = [google_project_service.apis]
}

# The bench client calls the Vertex AI endpoint (Phase 5) with its own identity.
resource "google_project_iam_member" "bench_client_vertex" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "${local.wi_principal}/ns/${var.k8s_namespace}/sa/bench-client"

  depends_on = [module.gke] # the workload identity pool exists once the cluster does
}

# The Custom Metrics Stackdriver Adapter reads Managed Prometheus metrics for the queue-depth HPA
# (k8s/envs/gke/*-autoscale).
resource "google_project_iam_member" "metrics_adapter" {
  project = var.project_id
  role    = "roles/monitoring.viewer"
  member  = "${local.wi_principal}/ns/custom-metrics/sa/custom-metrics-stackdriver-adapter"

  depends_on = [module.gke]
}

# Alerts only; a budget never stops spending. Credits are excluded so the alerts track real
# usage rather than the (credit-covered) net bill.
resource "google_billing_budget" "project" {
  count = var.billing_account_id == "" ? 0 : 1

  billing_account = var.billing_account_id
  display_name    = "${var.project_id} cap"

  budget_filter {
    projects               = ["projects/${data.google_project.this.number}"]
    credit_types_treatment = "EXCLUDE_ALL_CREDITS"
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.budget_usd)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.75
  }
  threshold_rules {
    threshold_percent = 1.0
  }

  depends_on = [google_project_service.apis]
}
