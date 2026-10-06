variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "region" {
  description = "Region of the repositories (same as the cluster, so pulls stay in-region)."
  type        = string
}

variable "images_repo_id" {
  description = "Repository for images built by this project."
  type        = string
}

variable "dockerhub_repo_id" {
  description = "Remote repository proxying Docker Hub."
  type        = string
}

variable "reader_members" {
  description = "IAM members allowed to pull from both repositories (e.g. the GKE node service account)."
  type        = list(string)
  default     = []
}
