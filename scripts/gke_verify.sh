#!/usr/bin/env bash
# Post-apply checks of the GKE cluster that need no GPU: node pools, control-plane access,
# Workload Identity (allowed and denied), Artifact Registry pulls and egress through Cloud NAT.
# Deploys k8s/envs/gke/shared (namespace, service accounts, bench client) along the way.
# Reads the values in .env (make passes them through) plus BENCH_IMAGE_TAG.
set -euo pipefail

: "${GCP_PROJECT_ID:?}" "${GCP_REGION:?}" "${GCP_ZONE:?}" "${GKE_CLUSTER_NAME:?}"
: "${AR_REPO:?}" "${AR_DOCKERHUB_REPO:?}" "${BENCH_IMAGE_TAG:?push the image first: scripts/push_bench_image.sh}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3
# kubectl authenticates to GKE through gke-gcloud-auth-plugin, which lives in the SDK's bin dir
# (Homebrew installs do not put it on PATH).
PATH="$(gcloud info --format='value(installation.sdk_root)' 2>/dev/null)/bin:$PATH"
CTX="gke_${GCP_PROJECT_ID}_${GCP_ZONE}_${GKE_CLUSTER_NAME}"
NS=llm-serving
K=(kubectl --context "$CTX")
KN=("${K[@]}" -n "$NS")
IMAGE="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/${AR_REPO}/llm-bench:${BENCH_IMAGE_TAG}"
FAILURES=0

step() { printf '\n== %s\n' "$*"; }
pass() { printf '  PASS  %s\n' "$*"; }
fail() {
  printf '  FAIL  %s\n' "$*"
  FAILURES=$((FAILURES + 1))
}
check() {
  local description=$1
  shift
  if "$@"; then pass "$description"; else fail "$description"; fi
}
equals() { [ "$1" = "$2" ]; }

# Python snippet run inside pods: call the Vertex AI API with the pod's Workload Identity token
# and print the HTTP status.
VERTEX_PROBE="
import json, urllib.error, urllib.request
md = 'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token'
token = json.load(urllib.request.urlopen(urllib.request.Request(md, headers={'Metadata-Flavor': 'Google'}), timeout=10))['access_token']
url = 'https://${GCP_REGION}-aiplatform.googleapis.com/v1/projects/${GCP_PROJECT_ID}/locations/${GCP_REGION}/endpoints'
try:
    urllib.request.urlopen(urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token}), timeout=30)
    print(200)
except urllib.error.HTTPError as e:
    print(e.code)
"

run_pod() { # run_pod <name> <service-account> <image> <command...>: run to completion, print logs
  local name=$1 sa=$2 image=$3
  shift 3
  "${KN[@]}" delete pod "$name" --ignore-not-found >/dev/null
  "${KN[@]}" run "$name" --image="$image" --restart=Never --quiet \
    --overrides="{\"spec\": {\"serviceAccountName\": \"$sa\", \"automountServiceAccountToken\": false}}" \
    --command -- "$@" >/dev/null
  "${KN[@]}" wait pod "$name" --for=jsonpath='{.status.phase}'=Succeeded --timeout=180s >/dev/null 2>&1 || true
  "${KN[@]}" logs "$name" 2>/dev/null | tail -n 1
  "${KN[@]}" delete pod "$name" --wait=false >/dev/null
}

step "Node pools"
cpu_ready=$("${K[@]}" get nodes -l cloud.google.com/gke-nodepool=cpu \
  -o jsonpath='{.items[*].status.conditions[?(@.type=="Ready")].status}')
check "one CPU node, Ready" equals "$cpu_ready" True
gpu_nodes=$("${K[@]}" get nodes -l cloud.google.com/gke-nodepool=gpu-l4-spot -o name | wc -l | tr -d ' ')
check "no GPU node while nothing requests a GPU" equals "$gpu_nodes" 0
pool=$(gcloud container node-pools describe gpu-l4-spot --cluster "$GKE_CLUSTER_NAME" \
  --location "$GCP_ZONE" --project "$GCP_PROJECT_ID" --format=json)
summary=$(printf '%s' "$pool" | "$PY" -c '
import json, sys
p = json.load(sys.stdin)
c, a = p["config"], p.get("autoscaling", {})
acc = c["accelerators"][0]
print(c["machineType"], c.get("spot"), acc["acceleratorType"], acc["acceleratorCount"],
      acc["gpuDriverInstallationConfig"]["gpuDriverVersion"], a.get("totalMinNodeCount", 0),
      a.get("totalMaxNodeCount"))')
echo "        gpu-l4-spot: $summary"
check "GPU pool is Spot g2-standard-8 + 1 L4, GKE-installed driver, autoscaling 0..1" \
  equals "$summary" "g2-standard-8 True nvidia-l4 1 LATEST 0 1"

step "Control plane access"
dns_ok() { "${K[@]}" get --raw /version >/dev/null; }
check "DNS endpoint (IAM-authorized) answers kubectl" dns_ok
ip_kubeconfig=$(mktemp)
KUBECONFIG="$ip_kubeconfig" gcloud container clusters get-credentials "$GKE_CLUSTER_NAME" \
  --location "$GCP_ZONE" --project "$GCP_PROJECT_ID" >/dev/null 2>&1
if kubectl --kubeconfig "$ip_kubeconfig" get --raw /version --request-timeout=10s >/dev/null 2>&1; then
  fail "public IP endpoint should refuse clients outside authorized networks"
else
  pass "public IP endpoint refuses this machine (no authorized networks)"
fi
rm -f "$ip_kubeconfig"

step "Shared resources (namespace, service accounts, PodMonitoring, bench client)"
BENCH_IMAGE_TAG="$BENCH_IMAGE_TAG" "$PY" "$ROOT/scripts/render_manifests.py" "$ROOT/k8s/envs/gke/shared" \
  | "${K[@]}" apply -f - >/dev/null
check "bench client rolled out (image pulled from Artifact Registry)" \
  "${KN[@]}" rollout status deploy/bench-client --timeout=300s
client_pool=$("${KN[@]}" get pods -l app.kubernetes.io/name=bench-client \
  -o jsonpath='{.items[0].spec.nodeName}' | xargs "${K[@]}" get node -o jsonpath='{.metadata.labels.cloud\.google\.com/gke-nodepool}')
check "bench client runs on the CPU pool" equals "$client_pool" cpu

step "Workload Identity"
status=$("${KN[@]}" exec deploy/bench-client -- python -c "$VERTEX_PROBE")
check "bench-client service account may call Vertex AI (HTTP $status)" equals "$status" 200
status=$(run_pod wi-denied inference "$IMAGE" python -c "$VERTEX_PROBE")
check "inference service account may not (HTTP $status, expected 403)" equals "$status" 403

step "Artifact Registry Docker Hub proxy"
proxy_image="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/${AR_DOCKERHUB_REPO}/library/busybox:1.37"
out=$(run_pod proxy-pull bench-client "$proxy_image" echo pulled-through-proxy)
check "busybox pulled through the remote repository" equals "$out" pulled-through-proxy

step "Egress through Cloud NAT (nodes have no external IPs)"
weights=$("${KN[@]}" exec deploy/bench-client -- python -c "
import json, urllib.request
info = json.load(urllib.request.urlopen('https://huggingface.co/api/models/Qwen/Qwen3-8B-AWQ?blobs=true', timeout=30))
print(round(sum(s.get('size', 0) for s in info['siblings'] if s['rfilename'].endswith('.safetensors')) / 1e9, 2))
" 2>/dev/null || echo error)
check "Hugging Face reachable from a pod (Qwen3-8B-AWQ weights: ${weights} GB)" \
  "$PY" -c "import sys; float(sys.argv[1])" "$weights"

echo
if [ "$FAILURES" -eq 0 ]; then echo "gke verify: all checks passed"; else echo "gke verify: $FAILURES check(s) FAILED"; fi
exit $((FAILURES > 0))
