import importlib.util
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("render", ROOT / "scripts" / "render_manifests.py")
render = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render)

ENV = {
    "GCP_PROJECT_ID": "my-project-123",
    "GCP_REGION": "us-central1",
    "AR_REPO": "llm-serving",
    "AR_DOCKERHUB_REPO": "dockerhub",
    "BENCH_IMAGE_TAG": "0479b66",
}


def test_substitutes_allowed_placeholders_and_leaves_kubernetes_syntax_alone():
    text = "image: ${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/x\nargs: [$(POD_IP)]\n"
    assert render.substitute(text, ENV) == (
        "image: us-central1-docker.pkg.dev/my-project-123/x\nargs: [$(POD_IP)]\n"
    )


@pytest.mark.parametrize(
    ("text", "env", "error"),
    [
        ("${NOT_ALLOWED}", ENV, "allow-list"),
        ("${GCP_PROJECT_ID}", {}, "unset"),
        ("${GCP_PROJECT_ID}", {"GCP_PROJECT_ID": ""}, "unset"),
        ("${GCP_PROJECT_ID}", {"GCP_PROJECT_ID": "x\nkind: Secret"}, "plain identifiers"),
        ("${GCP_PROJECT_ID}", {"GCP_PROJECT_ID": "a/b"}, "plain identifiers"),
    ],
)
def test_refuses_unknown_unset_or_unsafe_values(text, env, error):
    with pytest.raises(ValueError, match=error):
        render.substitute(text, env)


@pytest.mark.skipif(shutil.which("kubectl") is None, reason="kubectl not installed")
@pytest.mark.parametrize(
    "env_dir", sorted(p.parent for p in (ROOT / "k8s" / "envs").glob("*/*/kustomization.yaml"))
)
def test_every_env_renders_with_no_placeholder_left(env_dir):
    out = render.render(str(env_dir), ENV)
    assert "${" not in out
    assert "kind: " in out
