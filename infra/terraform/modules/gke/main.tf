# Zonal GKE Standard cluster (the GKE free tier covers one zonal cluster's management fee per
# billing account) with two node pools:
#   cpu         - one on-demand node for system pods and the bench client
#   gpu-l4-spot - Spot g2-standard-8 (1x L4), autoscaling 0..N, so it costs nothing while idle.
# GKE itself taints the GPU pool nvidia.com/gpu=present:NoSchedule and labels it with
# cloud.google.com/gke-accelerator and cloud.google.com/gke-spot, which k8s/ relies on.

resource "google_service_account" "nodes" {
  project      = var.project_id
  account_id   = "${var.cluster_name}-nodes"
  display_name = "GKE nodes of ${var.cluster_name} (least privilege, not the default compute SA)"
}

resource "google_project_iam_member" "nodes" {
  project = var.project_id
  role    = "roles/container.defaultNodeServiceAccount"
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_container_cluster" "this" {
  project  = var.project_id
  name     = var.cluster_name
  location = var.zone

  network    = var.network_id
  subnetwork = var.subnetwork_id

  # Node pools are managed below; GKE needs an initial pool, which is removed right away.
  remove_default_node_pool = true
  initial_node_count       = 1
  # `make down` has to be able to delete the cluster.
  deletion_protection = false

  release_channel {
    channel = "REGULAR"
  }

  networking_mode = "VPC_NATIVE"
  ip_allocation_policy {
    cluster_secondary_range_name  = var.pods_range_name
    services_secondary_range_name = var.services_range_name
  }

  # Private nodes; the control plane is reached through its DNS endpoint, which is authorized by
  # IAM and works from any network. Its public IP endpoint accepts no external addresses.
  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
  }
  control_plane_endpoints_config {
    dns_endpoint_config {
      allow_external_traffic = true
    }
  }
  master_authorized_networks_config {}

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  # Managed Prometheus scrapes vLLM / SGLang metrics (PodMonitoring in k8s/envs/gke/shared);
  # DCGM adds GPU utilization and memory metrics from the GPU nodes.
  monitoring_config {
    enable_components = ["SYSTEM_COMPONENTS", "DCGM"]
    managed_prometheus {
      enabled = true
    }
  }

  # Remove an idle GPU node sooner than the default profile would.
  cluster_autoscaling {
    autoscaling_profile = "OPTIMIZE_UTILIZATION"
  }

  depends_on = [google_project_iam_member.nodes]
}

resource "google_container_node_pool" "cpu" {
  project    = var.project_id
  name       = "cpu"
  cluster    = google_container_cluster.this.id
  location   = var.zone
  node_count = 1

  management {
    auto_repair  = true
    auto_upgrade = true
  }
  # No surge node: replace in place, so upgrades never need CPU quota beyond the running nodes.
  upgrade_settings {
    max_surge       = 0
    max_unavailable = 1
  }

  node_config {
    machine_type    = var.cpu_machine_type
    disk_type       = "pd-balanced"
    disk_size_gb    = 50
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    workload_metadata_config {
      mode = "GKE_METADATA"
    }
    shielded_instance_config {
      enable_secure_boot = true
    }
    gcfs_config {
      enabled = true
    }
  }
}

resource "google_container_node_pool" "gpu" {
  project            = var.project_id
  name               = "gpu-l4-spot"
  cluster            = google_container_cluster.this.id
  location           = var.zone
  node_locations     = var.gpu_zones
  initial_node_count = 0

  autoscaling {
    total_min_node_count = 0
    total_max_node_count = var.gpu_max_nodes
    # Take Spot capacity in whichever allowed zone has it.
    location_policy = "ANY"
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }
  # A surge node would need a second GPU (quota); replace in place instead.
  upgrade_settings {
    max_surge       = 0
    max_unavailable = 1
  }

  node_config {
    machine_type    = var.gpu_machine_type
    spot            = true
    disk_type       = "pd-balanced"
    disk_size_gb    = 100
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    guest_accelerator {
      type  = var.gpu_type
      count = 1
      gpu_driver_installation_config {
        gpu_driver_version = var.gpu_driver_version
      }
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }
    shielded_instance_config {
      enable_secure_boot = true
    }
    # Image streaming: multi-GB serving images start before they are fully pulled.
    gcfs_config {
      enabled = true
    }
  }

  lifecycle {
    ignore_changes = [initial_node_count]
  }
}
