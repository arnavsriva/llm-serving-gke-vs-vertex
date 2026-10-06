variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "region" {
  description = "GCP region."
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "Zone of the cluster, its CPU node and (by default) the GPU pool."
  type        = string
  default     = "us-central1-a"
}

variable "cluster_name" {
  description = "GKE cluster name (also names the VPC)."
  type        = string
  default     = "llm-serving-dev"
}

variable "k8s_namespace" {
  description = "Namespace of the workloads (k8s/common/namespace.yaml)."
  type        = string
  default     = "llm-serving"
}

variable "cpu_machine_type" {
  description = "Machine type of the single on-demand CPU node."
  type        = string
  default     = "e2-standard-4"
}

variable "gpu_machine_type" {
  description = "Machine type for the GPU node pool."
  type        = string
  default     = "g2-standard-8"
}

variable "gpu_type" {
  description = "Accelerator type."
  type        = string
  default     = "nvidia-l4"
}

variable "gpu_driver_version" {
  description = "GKE-managed NVIDIA driver version: LATEST or DEFAULT."
  type        = string
  default     = "LATEST"
}

variable "gpu_zones" {
  description = "Zones for the GPU pool; empty means the cluster's zone only."
  type        = list(string)
  default     = []
}

variable "gpu_pool_max_nodes" {
  description = "Maximum GPU nodes (the pool always scales down to 0)."
  type        = number
  default     = 1
}

variable "artifact_registry_repo" {
  description = "Artifact Registry Docker repository for this project's images."
  type        = string
  default     = "llm-serving"
}

variable "dockerhub_remote_repo" {
  description = "Artifact Registry remote repository that proxies Docker Hub."
  type        = string
  default     = "dockerhub"
}

variable "billing_account_id" {
  description = "Billing account for the budget alert (XXXXXX-XXXXXX-XXXXXX); empty skips the budget."
  type        = string
  default     = ""
}

variable "budget_usd" {
  description = "Budget amount; alerts at 50%, 75% and 100% of it."
  type        = number
  default     = 90
}
