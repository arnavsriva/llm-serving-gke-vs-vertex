output "images_repo_url" {
  description = "Prefix for this project's images, e.g. <url>/llm-bench:<tag>."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "dockerhub_repo_url" {
  description = "Prefix that replaces docker.io, e.g. <url>/vllm/vllm-openai:<tag>."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.dockerhub.repository_id}"
}
