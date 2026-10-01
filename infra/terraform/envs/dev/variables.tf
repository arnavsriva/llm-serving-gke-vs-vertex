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
  description = "Zone for the GPU node pool (L4 availability varies by zone)."
  type        = string
  default     = "us-central1-a"
}

variable "cluster_name" {
  description = "GKE cluster name."
  type        = string
  default     = "llm-serving-dev"
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

variable "gpu_count_per_node" {
  description = "GPUs per node."
  type        = number
  default     = 1
}

variable "gpu_pool_min_nodes" {
  description = "Minimum GPU nodes (keep 0 to scale to zero)."
  type        = number
  default     = 0
}

variable "gpu_pool_max_nodes" {
  description = "Maximum GPU nodes."
  type        = number
  default     = 1
}

variable "artifact_registry_repo" {
  description = "Artifact Registry Docker repository name."
  type        = string
  default     = "llm-serving"
}
