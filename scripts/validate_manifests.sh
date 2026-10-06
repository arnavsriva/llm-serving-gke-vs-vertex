#!/usr/bin/env bash
# Render every kustomize env under k8s/envs and validate the output with kubeconform. CRDs such
# as GKE's PodMonitoring are checked against the datreeio CRDs-catalog schemas. No cluster needed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CRD_SCHEMAS='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'

failed=0
for kustomization in "$ROOT"/k8s/envs/*/*/kustomization.yaml; do
  env_dir="$(dirname "$kustomization")"
  name="${env_dir#"$ROOT"/}"
  if kubectl kustomize "$env_dir" \
    | kubeconform -strict -summary -output text \
      -schema-location default -schema-location "$CRD_SCHEMAS" >/tmp/kubeconform.$$ 2>&1; then
    printf 'ok    %-32s %s\n' "$name" "$(tail -n 1 /tmp/kubeconform.$$)"
  else
    printf 'FAIL  %s\n' "$name"
    cat /tmp/kubeconform.$$
    failed=1
  fi
done
rm -f /tmp/kubeconform.$$
exit "$failed"
