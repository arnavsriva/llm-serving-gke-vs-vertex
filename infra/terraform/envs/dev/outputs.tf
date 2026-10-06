output "cluster_name" {
  value = module.gke.cluster_name
}

output "cluster_location" {
  value = module.gke.location
}

output "get_credentials" {
  description = "Writes a kubeconfig entry that reaches the control plane through its DNS endpoint."
  value       = "gcloud container clusters get-credentials ${module.gke.cluster_name} --location ${module.gke.location} --project ${var.project_id} --dns-endpoint"
}

output "images_repo_url" {
  value = module.registry.images_repo_url
}

output "dockerhub_repo_url" {
  value = module.registry.dockerhub_repo_url
}

output "node_service_account" {
  value = module.gke.node_service_account
}
