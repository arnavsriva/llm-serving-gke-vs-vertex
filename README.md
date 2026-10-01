# LLM Serving on GKE vs. Vertex AI

> Serving **Qwen3-8B (AWQ)** on a single NVIDIA L4 three ways — vLLM on GKE, TGI on GKE, and vLLM on
> a Vertex AI endpoint — and comparing latency, throughput, and cost under identical load. Plus LoRA
> fine-tuning and a debugging playbook of real, reproduced failures.

**Status:** 🚧 Scaffold only. No benchmarks have been run yet. Every number in this README will come
from a saved run in [`results/`](results/).

---

## Overview

The question a client actually asks: *"Should we self-host our open model on GKE, or use a managed
Vertex AI endpoint?"* This repo answers it with measurements rather than opinions:

- Same model, same quantization, same GPU type (L4), same region (`us-central1`).
- Same load generator, same prompt-length distribution, same concurrency sweep.
- Metrics: **TTFT**, **inter-token latency (ITL)**, **output throughput vs concurrency**, and
  **$ per 1M output tokens**.

## Architecture

_TODO (Phase: infra)._ Diagram of:

- GKE Standard cluster with a default CPU pool and a separate **spot L4 GPU node pool** (autoscaling,
  min 0), tainted so only inference pods land there.
- vLLM and TGI Deployments behind ClusterIP Services; HPA; PodDisruptionBudgets.
- Artifact Registry for images; Workload Identity for pod → GCP access.
- Vertex AI Model Registry → Endpoint running the custom vLLM container.
- Load generator running from a single client location against all three targets.

## Quickstart

> ⚠️ Commands marked 💲 create billable resources. Read [Cost](#cost) first.

```bash
cp .env.example .env                                            # fill in your values
cp infra/terraform/envs/dev/terraform.tfvars.example infra/terraform/envs/dev/terraform.tfvars
make setup                                                      # local Python env + pre-commit
make lint test                                                  # no cloud access needed
make tf-plan                                                    # plan only
make up                                                         # 💲 create infra
make deploy-vllm                                                # 💲 GPU node scales up
make bench TARGET=vllm-gke
make down                                                       # tear everything down
```

## Serving Setups

### vLLM on GKE
_TODO._ Image, args, readiness probe on model load, HPA signal, PDB, spot preemption notes.

### TGI on GKE
_TODO._

### vLLM on Vertex AI
_TODO._ Custom container → Artifact Registry → Model Registry → Endpoint → undeploy.

## Benchmark Method

_TODO._ Will document: client location, warm-up, concurrency sweep, prompt/output length
distributions, request count per level, how TTFT/ITL are measured from the streamed response,
and how cost is computed from confirmed node-hour prices.

## Results

> **Placeholder — no runs yet.** This section will only contain numbers produced by saved runs in
> [`results/`](results/), each with its raw CSV, config, and timestamp.

| Setup | TTFT p50 | TTFT p95 | ITL p50 | Max sustained tok/s | $ / 1M output tokens |
|---|---|---|---|---|---|
| vLLM on GKE | — | — | — | — | — |
| TGI on GKE | — | — | — | — | — |
| vLLM on Vertex AI | — | — | — | — | — |

### What I'd recommend to a client and why
_TODO — written only after results exist._

## Fine-Tuning

_TODO._ LoRA on Qwen3-1.7B/4B with a public instruction dataset on a spot L4; registered in Model
Registry; served from a Vertex endpoint; before/after eval.

## Debugging Playbook

Reproduced failures, each written up as a postmortem (symptoms → isolation → root cause → fix) in
[`docs/postmortems/`](docs/postmortems/):

- [ ] CUDA out-of-memory
- [ ] Driver / CUDA version mismatch
- [ ] IAM permission denied from a pod
- [ ] Pod stuck `Pending` (quota or taints)
- [ ] Prompt-template regression

## Cost

- Budget for this project: **~$90** of GCP credits.
- All GPU nodes are **Spot**; GPU node pool scales to **zero**; Vertex endpoints are **undeployed**
  right after each benchmark session.
- _TODO:_ itemised actual spend per phase.

## Teardown

```bash
make down
```

_TODO._ Will document exactly what `make down` removes and how to verify nothing billable remains.

## Project Structure

```
.
├── infra/terraform/
│   ├── modules/          # network, gke, gpu node pool, artifact registry, iam
│   └── envs/dev/         # root module, GCS remote state
├── k8s/
│   ├── common/           # namespace, shared config
│   ├── vllm/             # Deployment, Service, HPA, PDB
│   └── tgi/
├── vertex/
│   ├── container/        # custom vLLM serving image
│   └── deploy/           # Model Registry upload, endpoint deploy/undeploy
├── bench/                # async load generator, analysis, plots, cost table
├── finetune/             # LoRA training + eval
├── results/              # saved runs — source of truth for every number
├── docs/postmortems/     # debugging playbook
├── tests/                # unit tests (no cloud)
└── scripts/              # quota check, teardown helpers
```

## License

[MIT](LICENSE)
