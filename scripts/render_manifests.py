"""Render a kustomize env and fill its ``${VAR}`` placeholders from the environment.

    python scripts/render_manifests.py k8s/envs/gke/vllm | kubectl apply -f -

Only the variables in ``ALLOWED`` are substituted, and their values must be plain identifiers.
Rendering fails if any placeholder is unknown or unset, so a manifest with a missing project ID
or registry path never reaches a cluster. Kubernetes' own ``$(VAR)`` syntax is left alone.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Mapping

ALLOWED = ("GCP_PROJECT_ID", "GCP_REGION", "AR_REPO", "AR_DOCKERHUB_REPO", "BENCH_IMAGE_TAG")
PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
SAFE_VALUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def substitute(text: str, env: Mapping[str, str]) -> str:
    names = {m.group(1) for m in PLACEHOLDER.finditer(text)}
    unknown = sorted(names - set(ALLOWED))
    if unknown:
        raise ValueError(f"placeholders not in the allow-list: {', '.join(unknown)}")
    missing = sorted(n for n in names if not env.get(n))
    if missing:
        raise ValueError(f"unset environment variables: {', '.join(missing)}")
    unsafe = sorted(n for n in names if not SAFE_VALUE.match(env[n]))
    if unsafe:
        raise ValueError(f"values must be plain identifiers: {', '.join(unsafe)}")
    return PLACEHOLDER.sub(lambda m: env[m.group(1)], text)


def render(env_dir: str, env: Mapping[str, str]) -> str:
    built = subprocess.run(
        ["kubectl", "kustomize", env_dir], check=True, capture_output=True, text=True
    ).stdout
    return substitute(built, env)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <kustomize-env-dir>", file=sys.stderr)
        return 2
    try:
        sys.stdout.write(render(argv[1], os.environ))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(exc.stderr, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
