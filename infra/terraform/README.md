# Terraform

One environment (`envs/dev`) built from three small modules. State lives in a versioned GCS
bucket. All inputs come from `.env` through the Makefile (`TF_VAR_*`); `terraform.tfvars` can
override them.

| Module | Creates |
|---|---|
| `network` | VPC, subnet with pod/Service secondary ranges and Private Google Access, Cloud Router + Cloud NAT |
| `gke` | zonal GKE Standard cluster, least-privilege node service account, `cpu` pool (1 × e2-standard-4, on-demand), `gpu-l4-spot` pool (Spot g2-standard-8 + 1 L4, autoscaling 0–1) |
| `registry` | Artifact Registry: `llm-serving` (own images) and `dockerhub` (remote repository proxying Docker Hub) |

`envs/dev` also enables the required APIs, grants Workload Identity roles to Kubernetes service
accounts, and creates a $90 budget with alerts at 50/75/100% of spend before credits.

## Choices

- **Zonal cluster.** The GKE free tier covers one zonal cluster's management fee ($0.10/h) per
  billing account.
- **Private nodes, Cloud NAT.** Nodes have no external IPs. Google APIs go through Private Google
  Access; Hugging Face downloads go through NAT.
- **Control plane via its DNS endpoint.** Access is authorized by IAM, so kubectl works from any
  network without an IP allow-list. The public IP endpoint has authorized networks enabled with
  no CIDRs, so it rejects all outside clients (`make gke-verify` checks this).
- **GPU pool.** Spot, scales to zero, `location_policy = ANY` (add zones in `gpu_zones` if Spot
  capacity is short), GKE-installed `LATEST` NVIDIA driver, image streaming, and in-place
  upgrades (`max_surge = 0`) because a surge node would need a second GPU. GKE adds the
  `nvidia.com/gpu=present:NoSchedule` taint and the accelerator/Spot labels that `k8s/` relies on.
- **Observability.** Managed Prometheus (scrapes vLLM/SGLang `/metrics`) and DCGM GPU metrics.
- **Identity.** Workload Identity Federation grants roles straight to Kubernetes service
  accounts: `bench-client` gets `roles/aiplatform.user`, the metrics adapter gets
  `roles/monitoring.viewer`. The server identity (`inference`) gets nothing, and the nodes run as
  a dedicated service account, not the default compute one.
- **Teardown.** `deletion_protection = false` and `disable_on_destroy = false`, so `make down`
  removes everything except the enabled APIs and the state bucket.

## Workflow

```bash
make quota          # read-only: GPU/CPU quotas, flags blockers
make tf-bootstrap   # once: state bucket (a few KB, ~$0)
make tf-plan        # read-only plan, saved to tfplan
make up             # apply the saved plan (~10 min) and fetch kubectl credentials
make gke-verify     # push the bench image; check pools, endpoint lockdown, WI, AR, NAT
make down           # destroy (asks for confirmation)
```

Quota needed for GPU nodes: `PREEMPTIBLE_NVIDIA_L4_GPUS` ≥ 1 in the region **and** the global
`GPUS_ALL_REGIONS` ≥ 1. New projects often start with the global quota at 0. On a Free Trial
billing account GPUs are unavailable altogether: node creation fails with `UNSUPPORTED_OPERATION`
("billing account is currently in the free tier where non-TPU accelerators are not available"),
which GKE only surfaces as `FailedScaleUp: Internal error`
([evidence](../../docs/postmortems/evidence/2026-10-06-gpu-pod-pending/)). Upgrade the billing
account, then request the quota.

## Verified

On 2026-10-06 the cluster was created with `make up` (26 resources, 12.5 min), all 11
`make gke-verify` checks passed
([log](../../results/validation/gke/20261006T161339Z_gke_verify.txt)), and it was destroyed with
`make down`.
