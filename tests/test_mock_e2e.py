"""End-to-end: the real client and runner against the in-process mock, with known timings.

Tolerances are loose enough for a noisy CI runner; `python -m bench smoke` runs the tighter,
multi-level version of the same checks against the mock in its own process.
"""

import asyncio
from dataclasses import replace

import aiohttp
import pytest
from aiohttp.test_utils import TestServer

from bench.mock_server import ENGINE, MockConfig, build_app
from bench.report import load_run, read_csv
from bench.runner import RunConfig, run_benchmark

BASE = RunConfig(
    base_url="",
    target="mock",
    model="mock-model",
    concurrency=[1],
    requests_per_level=6,
    prompt_len="fixed:40",
    output_len="fixed:8",
    pause_between_levels_s=0.0,
    quiet=True,
)


def run_against_mock(mock: MockConfig, tmp_path, **overrides):
    async def scenario():
        server = TestServer(build_app(mock))
        await server.start_server()
        try:
            base_url = str(server.make_url("/")).rstrip("/")
            cfg = replace(BASE, base_url=base_url, out_dir=tmp_path, **overrides)
            return await run_benchmark(cfg)
        finally:
            await server.close()

    run_dir = asyncio.run(scenario())
    return run_dir, load_run(run_dir)


def test_latency_matches_injected_timings(tmp_path):
    mock = MockConfig(decode_base_ms=20.0, decode_per_seq_ms=0.0, prefill_base_ms=30.0)
    _, run = run_against_mock(mock, tmp_path)
    (row,) = run.rows
    assert row["n_errors"] == 0 and row["n_ok"] == 6
    # First token = prefill (30 ms) + one decode step (20 ms); later tokens = one step each.
    assert row["ttft_ms_p50"] == pytest.approx(50.0, rel=0.2)
    assert row["tpot_ms_p50"] == pytest.approx(20.0, rel=0.15)
    assert row["itl_ms_p50"] == pytest.approx(20.0, rel=0.15)
    assert row["output_tokens_mean"] == 8
    assert row["prompt_tokens_mean"] == 40
    assert row["token_source"] == "usage"


def test_saturated_throughput_and_littles_law(tmp_path):
    # Four workers, two engine slots: requests always queue, so throughput = 2 / step.
    mock = MockConfig(decode_base_ms=10.0, decode_per_seq_ms=0.0, max_num_seqs=2)
    run_dir, run = run_against_mock(
        mock, tmp_path, concurrency=[4], requests_per_level=16, output_len="fixed:12"
    )
    (row,) = run.rows
    assert row["n_ok"] == 16
    assert row["output_tok_s"] == pytest.approx(200.0, rel=0.15)
    assert row["tpot_ms_p50"] == pytest.approx(10.0, rel=0.15)
    assert 0.9 <= row["littles_law_ratio"] <= 1.05
    # Measured requests come from the level's fixed index block, after the 4 warm-up ones.
    indices = sorted(r["index"] for r in read_csv(run_dir / "requests.csv"))
    assert indices == list(range(4, 20))
    assert run.meta["levels"][0]["warmup"] == 4 and run.meta["levels"][0]["measured"] == 16


def test_chat_api_without_usage_counts_chunks(tmp_path):
    mock = MockConfig(decode_base_ms=5.0, decode_per_seq_ms=0.0)
    _, run = run_against_mock(mock, tmp_path, api="chat", stream_usage=False, concurrency=[1, 2])
    for row in run.rows:
        assert row["n_errors"] == 0
        assert row["token_source"] == "chunks"
        assert row["output_tokens_mean"] == 8  # the empty role chunk is not a token
        assert row["prompt_tokens_mean"] is None


def test_server_errors_are_recorded_not_raised(tmp_path):
    mock = MockConfig(decode_base_ms=5.0, error_rate=1.0)
    run_dir, run = run_against_mock(mock, tmp_path)
    (row,) = run.rows
    assert row["n_ok"] == 0 and row["n_errors"] == 6
    errors = {r["error"] for r in read_csv(run_dir / "requests.csv")}
    assert all(e.startswith("HTTP 500") for e in errors)


def test_health_reports_loading_until_ready():
    async def scenario():
        server = TestServer(build_app(MockConfig(load_delay_s=60)))
        await server.start_server()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(server.make_url("/health")) as resp:
                    assert resp.status == 503
                body = {"prompt": "hi", "max_tokens": 2}
                async with session.post(server.make_url("/v1/completions"), json=body) as resp:
                    assert resp.status == 503
        finally:
            await server.close()

    asyncio.run(scenario())


def test_client_disconnect_frees_the_engine_slot():
    async def scenario():
        app = build_app(MockConfig(decode_base_ms=5.0, decode_per_seq_ms=0.0))
        server = TestServer(app)
        await server.start_server()
        try:
            async with aiohttp.ClientSession() as session:
                body = {"prompt": "hi", "max_tokens": 100_000, "stream": True}
                resp = await session.post(server.make_url("/v1/completions"), json=body)
                await resp.content.readany()
                assert len(app[ENGINE].running) == 1
                resp.close()
            for _ in range(200):
                if not app[ENGINE].running:
                    break
                await asyncio.sleep(0.01)
            assert not app[ENGINE].running
        finally:
            await server.close()

    asyncio.run(scenario())
