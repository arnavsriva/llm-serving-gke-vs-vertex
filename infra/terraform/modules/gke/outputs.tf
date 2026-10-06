output "cluster_name" {
  value = google_container_cluster.this.name
}

output "location" {
  value = google_container_cluster.this.location
}

output "dns_endpoint" {
  description = "IAM-authorized DNS endpoint of the control plane."
  value       = google_container_cluster.this.control_plane_endpoints_config[0].dns_endpoint_config[0].endpoint
}

output "node_service_account" {
  value = google_service_account.nodes.email
}

output "workload_pool" {
  value = google_container_cluster.this.workload_identity_config[0].workload_pool
}
