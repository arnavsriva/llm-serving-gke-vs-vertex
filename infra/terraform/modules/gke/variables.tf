variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "cluster_name" {
  description = "GKE cluster name (also prefixes the node service account, so at most 24 characters)."
  type        = string

  validation {
    condition     = length(var.cluster_name) <= 24
    error_message = "cluster_name must be at most 24 characters so the node service account ID fits."
  }
}

variable "zone" {
  description = "Zone of the (zonal) cluster and its CPU node pool."
  type        = string
}

variable "network_id" {
  description = "VPC self link or ID."
  type        = string
}

variable "subnetwork_id" {
  description = "Subnet self link or ID."
  type        = string
}

variable "pods_range_name" {
  description = "Name of the subnet's secondary range for pods."
  type        = string
}

variable "services_range_name" {
  description = "Name of the subnet's secondary range for Services."
  type        = string
}

variable "cpu_machine_type" {
  description = "Machine type of the single on-demand CPU node."
  type        = string
  default     = "e2-standard-4"
}

variable "gpu_machine_type" {
  description = "Machine type of the GPU node pool."
  type        = string
  default     = "g2-standard-8"
}

variable "gpu_type" {
  description = "Accelerator type attached to each GPU node."
  type        = string
  default     = "nvidia-l4"
}

variable "gpu_driver_version" {
  description = "GKE-managed NVIDIA driver: LATEST or DEFAULT for the node version."
  type        = string
  default     = "LATEST"

  validation {
    condition     = contains(["LATEST", "DEFAULT"], var.gpu_driver_version)
    error_message = "gpu_driver_version must be LATEST or DEFAULT."
  }
}

variable "gpu_zones" {
  description = "Zones the GPU pool may create nodes in. More zones means a better chance of Spot capacity, but a server outside the cluster's zone adds cross-zone latency."
  type        = list(string)
}

variable "gpu_max_nodes" {
  description = "Upper bound of the GPU pool's autoscaler (min is always 0)."
  type        = number
  default     = 1
}
