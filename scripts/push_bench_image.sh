#!/usr/bin/env bash
# Build the bench image (load generator + mock) for linux/amd64, the GKE node architecture, and
# push it to this project's Artifact Registry repo. Uses a throwaway Docker config with the gcloud
# credential helper, so ~/.docker/config.json is never modified. Prints the pushed image.
# Reads GCP_PROJECT_ID, GCP_REGION and AR_REPO (make passes .env through).
set -euo pipefail

: "${GCP_PROJECT_ID:?set GCP_PROJECT_ID (see .env.example)}"
: "${GCP_REGION:?set GCP_REGION (see .env.example)}"
: "${AR_REPO:?set AR_REPO (see .env.example)}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

commit=$(git -C "$ROOT" log -1 --format=%H)
if [ -n "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ]; then
  commit="${commit}-dirty"
fi
# Default tag: short commit, plus -dirty when the tracked files have uncommitted changes.
case "$commit" in
  *-dirty) tag="${BENCH_IMAGE_TAG:-${commit:0:12}-dirty}" ;;
  *) tag="${BENCH_IMAGE_TAG:-${commit:0:12}}" ;;
esac
image="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/${AR_REPO}/llm-bench:${tag}"

docker_config=$(mktemp -d)
trap 'rm -rf "$docker_config"' EXIT
# Docker finds CLI plugins (buildx) relative to its config dir, so point back at the real one's.
plugins="${DOCKER_CONFIG:-$HOME/.docker}/cli-plugins"
printf '{"credHelpers": {"%s-docker.pkg.dev": "gcloud"}, "cliPluginsExtraDirs": ["%s"]}\n' \
  "$GCP_REGION" "$plugins" >"$docker_config/config.json"
# Keep using the Docker engine of the current context (the throwaway config has no contexts).
DOCKER_HOST="${DOCKER_HOST:-$(docker context inspect --format '{{.Endpoints.docker.Host}}')}"
export DOCKER_HOST

DOCKER_CONFIG="$docker_config" docker buildx build --quiet \
  --platform linux/amd64 \
  --build-arg GIT_COMMIT="$commit" \
  -f "$ROOT/bench/Dockerfile" -t "$image" --push "$ROOT" >/dev/null
echo "$image"
