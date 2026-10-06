#!/usr/bin/env bash
# Local kind cluster shaped like the GKE one: a CPU node, plus a "GPU" node with GKE's L4 Spot
# labels and GPU taint that advertises one fake nvidia.com/gpu. GPU scheduling (nodeSelector,
# toleration, one GPU per server) can then be tested without a GPU.
#   scripts/kind.sh up | load | down
set -euo pipefail

CLUSTER="${KIND_CLUSTER:-llm-serving}"
IMAGE="${BENCH_IMAGE:-llm-bench:dev}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
K=(kubectl --context "kind-${CLUSTER}")  # never the current context, which may be a real cluster

load_image() {
  local commit
  commit=$(git -C "$ROOT" log -1 --format=%H 2>/dev/null || echo unknown)
  if [ -n "$(git -C "$ROOT" status --porcelain --untracked-files=no 2>/dev/null)" ]; then
    commit="${commit}-dirty"
  fi
  docker build -q -f "$ROOT/bench/Dockerfile" --build-arg GIT_COMMIT="$commit" -t "$IMAGE" "$ROOT" >/dev/null
  kind load docker-image "$IMAGE" --name "$CLUSTER"
}

up() {
  if kind get clusters | grep -qx "$CLUSTER"; then
    echo "kind cluster '$CLUSTER' already exists"
  else
    kind create cluster --name "$CLUSTER" --config "$ROOT/k8s/envs/kind/cluster.yaml" --wait 120s
  fi
  local node deadline
  node=$("${K[@]}" get nodes -l cloud.google.com/gke-accelerator=nvidia-l4 \
    -o jsonpath='{.items[0].metadata.name}')
  # Extended resources can be advertised by patching node status; the kubelet then reports it
  # as allocatable, and the scheduler treats it like a real device-plugin GPU.
  "${K[@]}" patch node "$node" --subresource=status --type=json \
    -p '[{"op": "add", "path": "/status/capacity/nvidia.com~1gpu", "value": "1"}]' >/dev/null
  deadline=$((SECONDS + 60))
  until [ "$("${K[@]}" get node "$node" -o jsonpath='{.status.allocatable.nvidia\.com/gpu}')" = "1" ]; do
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "fake GPU never became allocatable on $node" >&2
      exit 1
    fi
    sleep 1
  done
  echo "node $node: tainted nvidia.com/gpu=present:NoSchedule, allocatable nvidia.com/gpu=1"
  load_image
}

down() {
  kind delete cluster --name "$CLUSTER"
}

case "${1:-}" in
  up) up ;;
  load) load_image ;;
  down) down ;;
  *)
    echo "usage: $0 up|load|down" >&2
    exit 2
    ;;
esac
