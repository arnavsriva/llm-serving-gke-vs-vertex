import pytest

from bench.metrics import (
    SUMMARY_COLUMNS,
    measurement_window,
    percentile,
    summarize_level,
    tokens_per_chunk,
    windowed_output_tokens,
)
from bench.records import RequestRecord


def rec(phase, start, n_chunks, gap=0.1, first=0.1, usage=True, concurrency=2, **overrides):
    """A finished request whose chunks arrive at start+first, start+first+gap, ..."""
    chunks = [start + first + k * gap for k in range(n_chunks)]
    fields = {
        "index": 0,
        "concurrency": concurrency,
        "phase": phase,
        "prompt_words": 10,
        "max_tokens": n_chunks,
        "t_start": start,
        "t_first": chunks[0] if chunks else None,
        "t_end": (chunks[-1] if chunks else start) + 0.001,
        "chunk_times": chunks,
        "prompt_tokens": 10 if usage else None,
        "completion_tokens": n_chunks if usage else None,
        "finish_reason": "length",
        "http_status": 200,
    }
    return RequestRecord(**(fields | overrides))


def test_per_request_metrics():
    r = rec("measured", start=1.0, n_chunks=5, gap=0.02, first=0.25)
    assert r.ok
    assert r.ttft == pytest.approx(0.25)
    assert r.tpot == pytest.approx(0.02)
    assert r.itls == pytest.approx([0.02] * 4)
    assert r.e2e == pytest.approx(0.25 + 4 * 0.02 + 0.001)
    assert r.output_tokens == 5 and r.token_source == "usage"


def test_tpot_uses_server_token_count_when_chunks_carry_several_tokens():
    r = rec("measured", start=0.0, n_chunks=5, gap=0.04, first=0.1)
    r.completion_tokens = 9  # e.g. two tokens per chunk after the first
    assert r.tpot == pytest.approx((4 * 0.04) / 8)


def test_failed_and_cancelled_requests_are_not_ok():
    assert not rec("measured", 0, 3, error="HTTP 500: boom").ok
    assert not rec("cooldown", 0, 3, cancelled=True).ok
    assert rec("measured", 0, 1).tpot is None


def test_percentile_of_nothing_is_none():
    assert percentile([], 50) is None
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == pytest.approx(2.5)


def closed_loop(concurrency=2, n_requests=10, duration=1.0, tokens=10):
    """Each worker runs back-to-back 1 s requests over [0, 10): requests starting at 0 and 1
    are warm-up, 2..7 measured, 8 and 9 cool-down. Chunk offsets avoid window boundaries."""
    records = []
    for w in range(concurrency):
        for k in range(n_requests):
            phase = "warmup" if k < 2 else "measured" if k < 8 else "cooldown"
            records.append(
                rec(
                    phase,
                    start=k * duration,
                    n_chunks=tokens,
                    gap=duration / tokens,
                    first=duration / tokens / 2,
                    concurrency=concurrency,
                    index=w * 100 + k,
                )
            )
    return records


def test_window_spans_first_measured_start_to_last_measured_end():
    start, end = measurement_window(closed_loop())
    assert start == pytest.approx(2.0)
    assert end == pytest.approx(7.951)


def test_windowed_throughput_counts_every_phase_inside_the_window():
    records = closed_loop()
    # Window [2.0, 7.951]: per worker, chunks of the measured requests (60) plus none from
    # warm-up/cool-down, since chunk times sit mid-slot.
    assert windowed_output_tokens(records, 2.0, 7.951) == pytest.approx(2 * 60)
    # A window cutting through requests still counts the chunks inside it.
    assert windowed_output_tokens(records, 0.5, 1.5) == pytest.approx(2 * 10)


def test_tokens_per_chunk_weights_partial_requests():
    full = rec("measured", 0.0, 4)
    full.completion_tokens = 8  # 2 tokens per chunk
    partial = rec("cooldown", 0.0, 3, usage=False, cancelled=True)
    assert tokens_per_chunk([full, partial]) == pytest.approx(2.0)
    assert windowed_output_tokens([full, partial], 0.0, 10.0) == pytest.approx(8 + 3 * 2.0)


def test_summary_of_a_steady_closed_loop():
    row = summarize_level(closed_loop(), concurrency=2, target="t", lag_ms=[0.1, 0.2, 5.0])
    assert set(row) <= set(SUMMARY_COLUMNS)
    assert row["n_measured"] == 12 and row["n_ok"] == 12 and row["n_errors"] == 0
    assert row["ttft_ms_p50"] == pytest.approx(50.0)
    assert row["tpot_ms_p50"] == pytest.approx(100.0)
    assert row["itl_ms_p99"] == pytest.approx(100.0)
    assert row["output_tok_s"] == pytest.approx(120 / 5.951)
    # requests/s = token throughput / mean output tokens.
    assert row["requests_s"] == pytest.approx(12 / 5.951)
    assert row["littles_law_ratio"] == pytest.approx(12 / 5.951 * 0.951 / 2)
    assert row["token_source"] == "usage"
    assert row["client_lag_ms_max"] == 5.0
    assert "slo_attainment" not in row


def test_errors_are_excluded_from_latency_and_fail_the_slo():
    records = [
        rec("measured", 0.0, 5, gap=0.01, first=0.05),
        rec("measured", 0.0, 5, gap=0.05, first=0.05),
        rec("measured", 0.0, 0, usage=False, error="HTTP 500: boom", http_status=500),
    ]
    row = summarize_level(records, 3, "t", slo_ttft_ms=100, slo_tpot_ms=20)
    assert row["n_ok"] == 2 and row["n_errors"] == 1
    assert row["ttft_ms_p50"] == pytest.approx(50.0)
    assert row["slo_attainment"] == pytest.approx(1 / 3)


def test_chunk_counting_when_server_reports_no_usage():
    records = [rec("measured", 0.0, 6, usage=False)]
    row = summarize_level(records, 1, "t")
    assert row["token_source"] == "chunks"
    assert row["output_tokens_mean"] == 6
