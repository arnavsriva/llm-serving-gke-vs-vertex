# Kubernetes manifests

Kustomize bases for the two GKE serving stacks (vLLM and SGLang, both serving
`Qwen/Qwen3-8B-AWQ` on one NVIDIA L4), the in-cluster load generator, and the envs that combine
them for a local kind cluster or GKE.

```
k8s/
├── common/            namespace llm-serving, service account `inference`
├── vllm/              Deployment, Service, PDB        └── autoscaling/  HPA (component)
├── sglang/            Deployment, Service, PDB        └── autoscaling/  HPA (component)
├── bench-client/      load-generator pod + its own service account
├── components/
│   └── kind-mock/     swaps the model server for the CPU mock (kind only)
└── envs/
    ├── kind/          cluster.yaml, shared/, vllm/, sglang/
    └── gke/           shared/ (+ PodMonitoring), vllm/, sglang/, vllm-autoscale/, sglang-autoscale/
```

`shared` is applied once; each server env is applied and deleted on its own, so switching servers
never touches the namespace or the bench client. GKE envs contain `${...}` placeholders (project,
region, registry) that `scripts/render_manifests.py` fills from the environment. It refuses to
render if one is missing.

## Verified locally

`make kind-up kind-e2e` builds a kind cluster shaped like the GKE one: a CPU node and a "GPU"
node with GKE's Spot L4 labels, the `nvidia.com/gpu=present:NoSchedule` taint and one fake
`nvidia.com/gpu`. It then checks, with the CPU mock in place of the model servers:

- the bench client lands on the CPU node, and a server pod lands on the GPU node;
- a pod aimed at the GPU node without the toleration stays Pending, and the same pod with the
  toleration schedules there;
- while the model "loads", the pod is not Ready and its Service has no endpoints;
- a second server stays Pending (`Insufficient nvidia.com/gpu`) until the first is removed;
- the PDB allows exactly one voluntary disruption;
- a load-generator sweep from the bench client through each Service completes with no errors;
- every GKE env passes a server-side dry run against the API server.

CI runs the same checks on every push (`kind-e2e` job), plus `kubeconform -strict` on every
rendered env, including the `PodMonitoring` CRD.

## Design decisions

**Scheduling.** GKE taints GPU nodes with `nvidia.com/gpu=present:NoSchedule`, so only pods
that tolerate it can run there. The servers carry that toleration explicitly (rather than
relying on GKE's admission controller to add it), a `nodeSelector` for
`cloud.google.com/gke-accelerator: nvidia-l4` and `cloud.google.com/gke-spot: "true"`, and an
`nvidia.com/gpu: 1` limit. Nothing else tolerates the taint, so the bench client and system
workloads stay on the CPU pool and never compete with the server being measured.

**Probes.** vLLM and SGLang only open their HTTP port once the weights are downloaded and
loaded, so `GET /health` is also the model-loaded signal:
- *startup* allows up to 30 min (download + load + graph capture);
- *readiness* gates Service endpoints, so no request reaches a server that can't answer;
- *liveness* restarts a wedged engine, and only takes effect after startup succeeds.

**Rollouts.** `maxSurge: 0, maxUnavailable: 1`. A surge pod would need a second GPU, which
usually means a second node, so pods are replaced in place instead.

**Autoscaling.** The HPA scales on queue depth (`vllm:num_requests_waiting`,
`sglang:num_queue_reqs`): requests waiting for a batch slot are the direct sign that one replica
is no longer enough. GPU utilization saturates well before latency degrades, and CPU says
nothing about GPU load. On GKE, Managed Service for Prometheus scrapes `/metrics` (PodMonitoring
in `envs/gke/shared`), and the Custom Metrics Stackdriver Adapter serves the gauge to the HPA as
`prometheus.googleapis.com|<metric>|gauge`. A new replica costs minutes (node, image, model), so
scale-up needs a sustained queue and scale-down is slow. The target of 4 waiting requests is a
placeholder, to be set from the benchmark: the concurrency where TTFT starts to climb.

Benchmark deployments (`envs/gke/vllm`, `envs/gke/sglang`) have **no HPA**. Scaling during a
sweep would split traffic across replicas mid-measurement. `*-autoscale` is the production shape.

**PodDisruptionBudget.** `maxUnavailable: 1`. With a single replica, `minAvailable: 1` would block
every node drain and upgrade until GKE gives up and forces it. One disruption at a time keeps
drains working and, with two replicas, keeps one serving.

**Identity.** Pods run as the `inference` (servers) and `bench-client` Kubernetes service
accounts, with `automountServiceAccountToken: false`. On GKE, Workload Identity Federation grants
IAM roles directly to those principals (infra/terraform): no service account keys, no
annotations, and only the bench client gets Vertex AI access.

**Images.** Tags are pinned: `vllm/vllm-openai:v0.31.0-cu129`, because the default `v0.31.0`
image is built for CUDA 13.0 and needs an R580+ driver, and `lmsysorg/sglang:v0.5.21-runtime`.
On GKE they are pulled through an Artifact Registry remote repository for Docker Hub, which
avoids Docker Hub rate limits and allows image streaming (multi-GB images start without a full
pull). Both get checked against the node's driver (`nvidia-smi`) on first deploy.

## Spot preemption

GPU nodes are Spot VMs, so they can be reclaimed at any time with about 30 s notice. GKE splits
that grace period between regular and system pods. Our pods get roughly 15 s (GKE 1.35+ can
extend the total to 120 s on Standard node pools), so `terminationGracePeriodSeconds: 30` is an
upper bound, not a guarantee.

- **PDBs don't help.** Reclamation is involuntary and bypasses PodDisruptionBudgets.
- **In-flight streams are cut.** Clients see a dropped connection mid-generation. The load
  generator records these as errors per request instead of dropping them, and a benchmark level
  with preemption errors is re-run rather than reported.
- **Cold start dominates recovery.** A replacement needs a new Spot node (if any capacity is
  left in the zone), the image, and a 6.1 GB weight download (Qwen3-8B-AWQ, measured from a pod
  in `results/validation/gke/`) before it is Ready again. Image
  streaming helps with the image. Caching weights in GCS (GCS FUSE CSI) or on a disk image would
  remove the download.
- **For production traffic** on Spot: at least two replicas across zones, an on-demand fallback
  node pool (or a custom ComputeClass with Spot-then-on-demand priorities), and client retries.
  Generation requests have no side effects, so retrying them is safe.
