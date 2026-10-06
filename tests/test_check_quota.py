import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_quota", ROOT / "scripts" / "check_quota.py")
check_quota = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_quota)


def test_report_counts_blockers(capsys):
    blockers = check_quota.report(
        [
            ("GPUS_ALL_REGIONS", 0.0, 1, "any GPU VM"),  # below the need: blocker
            ("PREEMPTIBLE_NVIDIA_L4_GPUS", 1.0, 1, "Spot L4"),  # exactly enough
            ("SomeVertexQuota", None, 1, "unknown value counts as a blocker"),
        ]
    )
    out = capsys.readouterr().out
    assert blockers == 2
    assert out.count("BLOCKER") == 2
    assert "ok       PREEMPTIBLE_NVIDIA_L4_GPUS" in out
