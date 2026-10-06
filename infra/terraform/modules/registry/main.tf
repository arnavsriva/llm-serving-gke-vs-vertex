# Two Docker repositories:
#   images    - images this project builds (bench client, Vertex AI serving container)
#   dockerhub - pull-through cache of Docker Hub for the vLLM and SGLang images: no Docker Hub
#               rate limits, and GKE image streaming works only for Artifact Registry images.

resource "google_artifact_registry_repository" "images" {
  project       = var.project_id
  location      = var.region
  repository_id = var.images_repo_id
  format        = "DOCKER"
  description   = "Images built by this project (bench client, Vertex AI serving container)."
}

resource "google_artifact_registry_repository" "dockerhub" {
  project       = var.project_id
  location      = var.region
  repository_id = var.dockerhub_repo_id
  format        = "DOCKER"
  mode          = "REMOTE_REPOSITORY"
  description   = "Pull-through cache of Docker Hub (vLLM and SGLang images)."

  remote_repository_config {
    description = "Docker Hub"
    docker_repository {
      public_repository = "DOCKER_HUB"
    }
  }
}

resource "google_artifact_registry_repository_iam_member" "readers" {
  for_each = {
    for pair in setproduct(["images", "dockerhub"], var.reader_members) :
    "${pair[0]}/${pair[1]}" => { repo = pair[0], member = pair[1] }
  }

  project    = var.project_id
  location   = var.region
  repository = each.value.repo == "images" ? google_artifact_registry_repository.images.name : google_artifact_registry_repository.dockerhub.name
  role       = "roles/artifactregistry.reader"
  member     = each.value.member
}
