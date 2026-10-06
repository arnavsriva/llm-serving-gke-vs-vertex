variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "region" {
  description = "Region of the subnet, router and NAT."
  type        = string
}

variable "name" {
  description = "Name of the VPC; other resources are prefixed with it."
  type        = string
}

variable "nodes_cidr" {
  description = "Primary range of the subnet (node IPs)."
  type        = string
  default     = "10.10.0.0/20"
}

variable "pods_cidr" {
  description = "Secondary range for pod IPs."
  type        = string
  default     = "10.20.0.0/16"
}

variable "services_cidr" {
  description = "Secondary range for Service IPs."
  type        = string
  default     = "10.30.0.0/20"
}
