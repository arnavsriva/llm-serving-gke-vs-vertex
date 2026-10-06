#!/usr/bin/env bash
# End-to-end checks of the Kubernetes manifests on the kind cluster from `scripts/kind.sh up`,
# with the CPU mock standing in for vLLM and SGLang. Prints PASS/FAIL per check and exits 1 if
# any failed. Set E2E_OUT=<dir> to keep the results of the in-cluster load tests.
set -euo pipefail

CLUSTER="${KIND_CLUSTER:-llm-serving}"
NS=llm-serving
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${E2E_OUT:-}"
K=(kubectl --context "kind-${CLUSTER}")  # never the current context, which may be a real cluster
KN=("${K[@]}" -n "$NS")
FAILURES=0

step() { printf '\n== %s\n' "$*"; }
pass() { printf '  PASS  %s\n' "$*"; }
fail() {
  printf '  FAIL  %s\n' "$*"
  FAILURES=$((FAILURES + 1))
}
check() { # check <description> <command...>
  local description=$1
  shift
  if "$@"; then pass "$description"; else fail "$description"; fi
}
wait_until() { # wait_until <seconds> <command...>
  local deadline=$((SECONDS + $1))
  shift
  until "$@"; do
    [ "$SECONDS" -lt "$deadline" ] || return 1
    sleep 1
  done
}
contains() { case "$1" in *"$2"*) return 0 ;; *) return 1 ;; esac }

pod_of() { "${KN[@]}" get pods -l "app.kubernetes.io/name=$1" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null; }
node_of() { "${KN[@]}" get pods -l "app.kubernetes.io/name=$1" -o jsonpath='{.items[0].spec.nodeName}' 2>/dev/null; }
pod_exists() { [ -n "$(pod_of "$1")" ]; }
is_running() { [ "$("${KN[@]}" get pods -l "app.kubernetes.io/name=$1" -o jsonpath='{.items[0].status.phase}' 2>/dev/null)" = Running ]; }
is_ready() {
  [ "$("${KN[@]}" get pods -l "app.kubernetes.io/name=$1" \
    -o jsonpath='{.items[0].status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)" = True ]
}
not_ready() { ! is_ready "$1"; }
ready_endpoints() {
  "${KN[@]}" get endpointslices -l "kubernetes.io/service-name=$1" \
    -o jsonpath='{range .items[*].endpoints[*]}{.conditions.ready}{"\n"}{end}' | grep -c '^true$' || true
}
endpoints_are() { [ "$(ready_endpoints "$1")" = "$2" ]; }
scheduling_message() {
  "${KN[@]}" get events --field-selector "involvedObject.name=$1,reason=FailedScheduling" \
    -o jsonpath='{range .items[*]}{.message}{"\n"}{end}' 2>/dev/null | tail -n 1
}
has_scheduling_message() { [ -n "$(scheduling_message "$1")" ]; }
pdb_allows() { [ "$("${KN[@]}" get pdb "$1" -o jsonpath='{.status.disruptionsAllowed}' 2>/dev/null)" = "$2" ]; }

sweep() { # sweep <service> <target-label>: run the load generator in the bench-client pod
  "${KN[@]}" exec deploy/bench-client -- python -m bench run \
    --base-url "http://$1:8000" --target "$2" --model Qwen/Qwen3-8B-AWQ \
    --concurrency 1,4,8 --requests-per-level 16 --prompt-len uniform:50,150 \
    --output-len uniform:16,32 --pause-s 0.5 --out-dir /results --quiet | tail -n 1
}
sweep_ok() { # sweep_ok <run-dir-in-pod>: every measured request succeeded at every level
  "${KN[@]}" exec -i deploy/bench-client -- python - "$1" <<'PY'
import sys

from bench.report import load_run

rows = load_run(sys.argv[1]).rows
for r in rows:
    print(f"        c={r['concurrency']}: {r['n_ok']}/{r['n_measured']} ok, "
          f"{r['output_tok_s']:.0f} tok/s, TTFT p50 {r['ttft_ms_p50']:.1f} ms, "
          f"Little's-law ratio {r['littles_law_ratio']:.2f}")
sys.exit(any(r["n_errors"] or r["n_ok"] != r["n_measured"] for r in rows))
PY
}
keep_results() { # keep_results <run-dir-in-pod>
  if [ -n "$OUT" ]; then
    mkdir -p "$OUT"
    local log
    log=$(mktemp)
    # kubectl's websocket client can log spurious "Unknown stream id" lines; show stderr only on failure.
    if "${KN[@]}" cp "$(pod_of bench-client):$1" "$OUT/$(basename "$1")" >/dev/null 2>"$log"; then
      echo "        saved to $OUT/$(basename "$1")"
    else
      fail "copy results out of the bench client: $(cat "$log")"
    fi
    rm -f "$log"
  fi
}

gpu_node=$("${K[@]}" get nodes -l cloud.google.com/gke-accelerator=nvidia-l4 \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$gpu_node" ]; then
  echo "no kind cluster '$CLUSTER' with a GPU-labelled node: run scripts/kind.sh up first" >&2
  exit 1
fi

step "Clean slate"
for env in vllm sglang; do
  "${K[@]}" delete -k "$ROOT/k8s/envs/kind/$env" --ignore-not-found --wait=true >/dev/null 2>&1 || true
done
"${KN[@]}" delete pod no-toleration with-toleration --ignore-not-found >/dev/null 2>&1 || true
echo "  cluster kind-$CLUSTER, GPU node $gpu_node"

step "Shared: namespace, service accounts, bench client"
"${K[@]}" apply -k "$ROOT/k8s/envs/kind/shared" >/dev/null
"${KN[@]}" rollout status deploy/bench-client --timeout=120s >/dev/null
client_node=$(node_of bench-client)
check "bench client runs on a CPU node ($client_node), not the GPU node" [ "$client_node" != "$gpu_node" ]

step "vLLM (mock): GPU node only, no traffic until the model is loaded"
started=$SECONDS
"${K[@]}" apply -k "$ROOT/k8s/envs/kind/vllm" >/dev/null
check "pod starts" wait_until 60 is_running vllm
check "pod is scheduled on the GPU node" [ "$(node_of vllm)" = "$gpu_node" ]
check "while the model loads: pod is not Ready" not_ready vllm
check "while the model loads: Service vllm has no ready endpoints" endpoints_are vllm 0
check "pod becomes Ready once /health answers" wait_until 120 is_ready vllm
check "Service vllm then has exactly one ready endpoint" wait_until 30 endpoints_are vllm 1
echo "        ready $((SECONDS - started)) s after apply (mock load delay: 15 s)"

step "Taints keep everything else off the GPU node"
probe_pod() { # probe_pod <name> <tolerations-yaml>: a sleeper aimed at the GPU node, no GPU request
  "${KN[@]}" apply -f - >/dev/null <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: $1
spec:
  nodeSelector:
    cloud.google.com/gke-accelerator: nvidia-l4
  tolerations: $2
  securityContext:
    runAsNonRoot: true
    runAsUser: 10001
  containers:
    - name: sleep
      image: llm-bench:dev
      command: ["sleep", "3600"]
EOF
}
scheduled_on() { [ "$("${KN[@]}" get pod "$1" -o jsonpath='{.spec.nodeName}' 2>/dev/null)" = "$2" ]; }
probe_pod no-toleration "[]"
check "a pod aimed at the GPU node without the toleration cannot be scheduled" \
  wait_until 30 has_scheduling_message no-toleration
message=$(scheduling_message no-toleration)
echo "        scheduler: $message"
check "  ...because of a taint" contains "$message" "untolerated taint"
probe_pod with-toleration '[{"key": "nvidia.com/gpu", "operator": "Equal", "value": "present", "effect": "NoSchedule"}]'
check "  ...the GPU taint: the same pod with the toleration is scheduled there" \
  wait_until 30 scheduled_on with-toleration "$gpu_node"
"${KN[@]}" delete pod no-toleration with-toleration --wait=false >/dev/null

step "One GPU, one server: SGLang waits while vLLM holds the GPU"
"${K[@]}" apply -k "$ROOT/k8s/envs/kind/sglang" >/dev/null
wait_until 30 pod_exists sglang || true
sglang_pod=$(pod_of sglang)
check "SGLang pod cannot be scheduled" wait_until 30 has_scheduling_message "$sglang_pod"
message=$(scheduling_message "$sglang_pod")
echo "        scheduler: $message"
check "  ...because the only GPU is taken" contains "$message" "Insufficient nvidia.com/gpu"

step "PodDisruptionBudget"
check "PDB vllm allows one voluntary disruption (maxUnavailable: 1, never blocks drains)" \
  wait_until 30 pdb_allows vllm 1

step "In-cluster load test: bench client -> Service vllm"
run_dir=$(sweep vllm kind-vllm)
check "sweep finished with every measured request successful ($run_dir)" sweep_ok "$run_dir"
keep_results "$run_dir"

step "Switch servers: remove vLLM, SGLang takes the freed GPU"
"${K[@]}" delete -k "$ROOT/k8s/envs/kind/vllm" --wait=true >/dev/null
check "SGLang pod becomes Ready" wait_until 120 is_ready sglang
check "SGLang pod is on the GPU node" [ "$(node_of sglang)" = "$gpu_node" ]
check "Service sglang has exactly one ready endpoint" wait_until 30 endpoints_are sglang 1
run_dir=$(sweep sglang kind-sglang)
check "sweep finished with every measured request successful ($run_dir)" sweep_ok "$run_dir"
keep_results "$run_dir"

step "GKE manifests accepted by the API server (server-side dry run)"
for env in vllm sglang vllm-autoscale sglang-autoscale; do
  if output=$("${K[@]}" apply --dry-run=server -k "$ROOT/k8s/envs/gke/$env" 2>&1); then
    pass "k8s/envs/gke/$env"
  else
    fail "k8s/envs/gke/$env: $output"
  fi
done

echo
if [ "$FAILURES" -eq 0 ]; then
  echo "kind e2e: all checks passed"
else
  echo "kind e2e: $FAILURES check(s) FAILED"
fi
exit $((FAILURES > 0))
