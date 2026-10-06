# Evidence: GPU pod stuck Pending, scale-up fails with "Internal error"

Captured on 2026-10-06 on `llm-serving-dev` (GKE 1.35.8), for the "pod stuck Pending (quota)"
postmortem in the debugging playbook. It happened for real on the first GPU request, not as a
staged fault.

**Trigger.** `k8s/envs/gke/vllm` applied to a cluster whose `gpu-l4-spot` pool was at 0 nodes.

| File | Shows |
|---|---|
| `k8s-events.txt` | `FailedScheduling` → `TriggeredScaleUp` (MIG 0→1) → `FailedScaleUp: ... Internal error` |
| `pod-status.txt` | the vLLM pod Pending with no node |
| `mig-errors.txt` | the real cause, from `gcloud compute instance-groups managed list-errors`: `UNSUPPORTED_OPERATION`, the billing account is in the free tier, where non-TPU accelerators are not available |
| `quota.txt` | `make quota` at the time: `GPUS_ALL_REGIONS` limit 0 |

**Takeaway for the write-up.** The Kubernetes events stop at a generic "Internal error". The
cause is only visible one layer down, in the managed instance group behind the node pool (or in
Cloud Logging for the cluster autoscaler). The fix is outside the cluster: upgrade the billing
account, then raise `GPUS_ALL_REGIONS`.
