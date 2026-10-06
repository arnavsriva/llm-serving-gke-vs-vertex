"""Show the quotas this project needs and flag the ones that block it. Read-only.

    python scripts/check_quota.py  (reads GCP_PROJECT_ID / GCP_REGION from the environment)

GKE GPU nodes need *both* the regional L4 quota (Spot: PREEMPTIBLE_NVIDIA_L4_GPUS) and the
global GPUS_ALL_REGIONS quota; the Vertex AI endpoint needs Vertex's own serving quota.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request

# (metric, minimum needed, what needs it)
COMPUTE_REGIONAL = [
    ("PREEMPTIBLE_NVIDIA_L4_GPUS", 1, "Spot L4 node for GKE serving (Phase 4)"),
    ("NVIDIA_L4_GPUS", 0, "on-demand L4 (not used: GPU nodes are Spot)"),
    ("CPUS", 12, "CPU node (4) + GPU node (8) vCPUs"),
]
COMPUTE_GLOBAL = [
    ("GPUS_ALL_REGIONS", 1, "any GPU VM at all, GKE nodes included"),
    ("CPUS_ALL_REGIONS", 12, "CPU node (4) + GPU node (8) vCPUs"),
]
VERTEX = [
    ("CustomModelServingL4GPUsPerProjectPerRegion", 1, "Vertex AI endpoint on L4 (Phase 5)"),
    (
        "CustomModelServingPreemptibleL4GPUsPerProjectPerRegion",
        0,
        "Spot Vertex endpoint (optional)",
    ),
]


def gcloud_json(*args: str) -> dict:
    out = subprocess.run(
        ["gcloud", *args, "--format=json"], check=True, capture_output=True, text=True
    )
    return json.loads(out.stdout)


def vertex_quotas(project: str, region: str) -> dict[str, float | None]:
    token = subprocess.run(
        ["gcloud", "auth", "print-access-token"], check=True, capture_output=True, text=True
    ).stdout.strip()
    headers = {"Authorization": f"Bearer {token}", "x-goog-user-project": project}
    base = (
        f"https://cloudquotas.googleapis.com/v1/projects/{project}/locations/global/"
        "services/aiplatform.googleapis.com/quotaInfos"
    )
    values: dict[str, float | None] = {}
    page_token = ""
    while True:
        url = f"{base}?pageSize=500" + (f"&pageToken={page_token}" if page_token else "")
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
            page = json.load(r)
        for info in page.get("quotaInfos", []):
            for dim in info.get("dimensionsInfos", []):
                if dim.get("dimensions", {}).get("region") == region:
                    value = dim.get("details", {}).get("value")
                    values[info["quotaId"]] = float(value) if value is not None else None
        page_token = page.get("nextPageToken", "")
        if not page_token:
            return values


def report(rows: list[tuple[str, float | None, int, str]]) -> int:
    blockers = 0
    for metric, limit, needed, purpose in rows:
        ok = limit is not None and limit >= needed
        blockers += not ok
        shown = "-" if limit is None else f"{limit:g}"
        status = "ok     " if ok else "BLOCKER"
        print(f"  {status}  {metric:56} limit {shown:>5}  need {needed:>2}  {purpose}")
    return blockers


def main() -> int:
    project = os.environ.get("GCP_PROJECT_ID")
    region = os.environ.get("GCP_REGION", "us-central1")
    if not project:
        print("set GCP_PROJECT_ID (see .env.example)", file=sys.stderr)
        return 2

    regional = {
        q["metric"]: q["limit"]
        for q in gcloud_json("compute", "regions", "describe", region, "--project", project)[
            "quotas"
        ]
    }
    global_ = {
        q["metric"]: q["limit"]
        for q in gcloud_json("compute", "project-info", "describe", "--project", project)["quotas"]
    }
    vertex = vertex_quotas(project, region)

    blockers = 0
    print(f"Compute Engine, {region}:")
    blockers += report([(m, regional.get(m), n, why) for m, n, why in COMPUTE_REGIONAL])
    print("Compute Engine, global:")
    blockers += report([(m, global_.get(m), n, why) for m, n, why in COMPUTE_GLOBAL])
    print(f"Vertex AI, {region}:")
    blockers += report([(m, vertex.get(m), n, why) for m, n, why in VERTEX])
    print(f"\n{blockers} blocker(s)" if blockers else "\nno blockers")
    return 1 if blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
