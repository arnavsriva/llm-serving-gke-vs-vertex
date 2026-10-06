from bench.metrics import SUMMARY_COLUMNS
from bench.records import RequestRecord
from bench.report import analyze_run, load_run, read_csv, write_json, write_raw
from bench.runner import make_run_id, redact_url


def test_raw_records_round_trip_into_summary(tmp_path):
    records = []
    for c in (1, 2):
        for k, phase in enumerate(("warmup", "measured", "measured", "cooldown")):
            start = float(k)
            records.append(
                RequestRecord(
                    index=k,
                    concurrency=c,
                    phase=phase,
                    prompt_words=5,
                    max_tokens=3,
                    t_start=start,
                    t_first=start + 0.1,
                    t_end=start + 0.31,
                    chunk_times=[start + 0.1, start + 0.2, start + 0.3],
                    completion_tokens=3,
                    prompt_tokens=7,
                    finish_reason="length",
                    http_status=200,
                )
            )
    write_json(
        tmp_path / "meta.json",
        {
            "target": "t",
            "slo": {"ttft_ms": 200, "tpot_ms": None},
            "levels": [{"concurrency": 1}, {"concurrency": 2}],
        },
    )
    write_raw(tmp_path, records, {1: [0.1], 2: [0.2]})

    rows = analyze_run(tmp_path)
    assert [r["concurrency"] for r in rows] == [1, 2]
    run = load_run(tmp_path)
    assert list(run.rows[0]) == list(SUMMARY_COLUMNS)
    assert run.rows[0]["n_measured"] == 2
    assert run.rows[0]["slo_attainment"] == 1.0
    assert run.rows[1]["client_lag_ms_max"] == 0.2
    requests = read_csv(tmp_path / "requests.csv")
    assert len(requests) == 4  # measured requests only, both levels
    assert all(r["ok"] is True for r in requests)


def test_redact_url_hides_project_and_credentials():
    url = (
        "https://user:secret@us-central1-aiplatform.googleapis.com/v1/projects/my-proj-123"
        "/locations/us-central1/endpoints/42:streamRawPredict?key=abc"
    )
    assert redact_url(url) == (
        "https://us-central1-aiplatform.googleapis.com/v1/projects/REDACTED"
        "/locations/us-central1/endpoints/42:streamRawPredict"
    )
    assert (
        redact_url("http://10.0.0.5:8000/v1/completions") == "http://10.0.0.5:8000/v1/completions"
    )


def test_run_id_is_filesystem_safe():
    run_id = make_run_id("vLLM on GKE!")
    assert run_id.endswith("_vllm-on-gke")
    assert run_id[:8].isdigit()
