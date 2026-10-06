import pytest

from bench.metrics import SUMMARY_COLUMNS
from bench.plots import _tick_label, assign_slots, plot_runs
from bench.report import write_csv, write_json


def fake_run(root, target, scale, fingerprint="abc"):
    run_dir = root / target
    run_dir.mkdir()
    write_json(
        run_dir / "meta.json",
        {
            "target": target,
            "model": "Qwen/Qwen3-8B-AWQ",
            "api": "completions",
            "workload": {
                "prompt_len_words": "uniform:200,800",
                "output_len_tokens": "uniform:128,384",
                "fingerprint": fingerprint,
            },
        },
    )
    rows = [
        {
            "target": target,
            "concurrency": c,
            "ttft_ms_p50": 40 * scale * c,
            "ttft_ms_p95": 60 * scale * c,
            "itl_ms_p50": 20 + scale * c,
            "itl_ms_p95": 25 + scale * c,
            "output_tok_s": 50 * c / (1 + c / 16) / scale,
        }
        for c in (1, 2, 4, 8, 16, 32, 64)
    ]
    write_csv(run_dir / "summary.csv", rows, SUMMARY_COLUMNS)
    return run_dir


def test_plots_are_written_for_each_chart_and_theme(tmp_path):
    runs = [fake_run(tmp_path, "vllm-gke", 1.0), fake_run(tmp_path, "tgi-gke", 1.3)]
    paths = plot_runs(runs, tmp_path / "out")
    assert len(paths) == 6
    assert all(p.exists() and p.stat().st_size > 10_000 for p in paths)


def test_single_run_plot(tmp_path):
    paths = plot_runs([fake_run(tmp_path, "mock", 1.0)], tmp_path / "out", themes=["light"])
    assert len(paths) == 3


def test_duplicate_targets_are_rejected(tmp_path):
    run = fake_run(tmp_path, "vllm-gke", 1.0)
    with pytest.raises(ValueError):
        plot_runs([run, run], tmp_path / "out")


@pytest.mark.parametrize(
    ("value", "label"),
    [(0, "0"), (2.5, "2.5"), (12.5, "12.5"), (17.5, "17.5"), (100, "100"), (5000, "5,000")],
)
def test_tick_labels_are_exact(value, label):
    assert _tick_label(value) == label


def test_targets_keep_their_colour_slot():
    assert assign_slots(["tgi-gke", "vllm-vertex"]) == {"tgi-gke": 1, "vllm-vertex": 2}
    assert assign_slots(["mock"]) == {"mock": 0}
    assert assign_slots(["vllm-gke", "mock"]) == {"vllm-gke": 0, "mock": 1}
