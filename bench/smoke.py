"""End-to-end validation of the load generator against the mock server.

The mock runs with zero prefill cost, so at concurrency ``c`` (``n = min(c, max_num_seqs)``
sequences running) a step takes ``step(n) = decode_base_ms + decode_per_seq_ms * n`` and:

* TPOT = ``step(n)``.
* Throughput = ``n / step(n)`` when requests queue (``c > max_num_seqs``). Otherwise a finished
  slot sits idle for one step, because its replacement arrives while the next step is already
  running, so throughput = ``n * L / (L + 1) / step(n)`` for mean output length ``L``. At
  ``c = 1`` the engine is idle when the request arrives, so there is no idle step.
* TTFT = ``step(1)`` plus one localhost HTTP round trip at ``c = 1`` (the round trip is measured
  with ``GET /health`` before the sweep). For ``2 <= c <= max_num_seqs`` a new request waits for
  the step in progress and then its own prefill step, so TTFT is about ``2 * step(n)``; the
  request's transport time overlaps the wait.
* Little's law: requests/s x mean E2E latency / ``c`` = 1 for a closed loop.

If the measured numbers match these, the client's timing, token counting and throughput
windowing are trustworthy; the same code then measures the real servers.
"""

from __future__ import annotations

import asyncio
import socket
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import aiohttp

from bench.mock_server import MockConfig
from bench.plots import plot_runs
from bench.report import load_run, write_csv
from bench.runner import RunConfig, run_benchmark

SMOKE_MOCK = MockConfig(decode_base_ms=10.0, decode_per_seq_ms=0.5, max_num_seqs=16)
SMOKE_LEVELS = [1, 2, 4, 8, 16, 32]
TOLERANCE = {"tpot_ms_p50": 0.10, "output_tok_s": 0.10, "ttft_ms_p50": 0.15}
LITTLES_LAW_BOUNDS = (0.95, 1.05)
VALIDATION_COLUMNS = (
    "concurrency",
    "metric",
    "expected",
    "measured",
    "rel_error",
    "tolerance",
    "passed",
)


def expected_metrics(
    mock: MockConfig, concurrency: int, mean_output_tokens: float, http_rtt_ms: float = 0.0
) -> dict[str, float]:
    n = min(concurrency, mock.max_num_seqs)
    step_s = mock.step_ms(n) / 1e3
    expected = {"tpot_ms_p50": step_s * 1e3}
    if concurrency == 1:
        expected["ttft_ms_p50"] = step_s * 1e3 + http_rtt_ms
        expected["output_tok_s"] = 1 / step_s
    elif concurrency <= mock.max_num_seqs:
        expected["ttft_ms_p50"] = 2 * step_s * 1e3
        tokens = mean_output_tokens
        expected["output_tok_s"] = n * tokens / (tokens + 1) / step_s
    else:
        expected["output_tok_s"] = n / step_s
    return expected


def validate(
    rows: list[dict[str, Any]], mock: MockConfig, http_rtt_ms: float = 0.0
) -> list[dict[str, Any]]:
    checks = [_check(None, "http_rtt_ms", None, http_rtt_ms, "info", True)]
    for row in rows:
        c = row["concurrency"]
        if row["n_errors"]:
            checks.append(_check(c, "n_errors", 0, row["n_errors"], None, False))
        expected = expected_metrics(mock, c, row["output_tokens_mean"], http_rtt_ms)
        for metric, value in expected.items():
            measured = row.get(metric)
            rel = _rel_error(value, measured)
            ok = rel is not None and abs(rel) <= TOLERANCE[metric]
            checks.append(_check(c, metric, value, measured, TOLERANCE[metric], ok))
        ratio = row.get("littles_law_ratio")
        lo, hi = LITTLES_LAW_BOUNDS
        ok = ratio is not None and lo <= ratio <= hi
        checks.append(_check(c, "littles_law_ratio", 1.0, ratio, f"[{lo}, {hi}]", ok))
    return checks


def _rel_error(expected: Any, measured: Any) -> float | None:
    if isinstance(measured, int | float) and isinstance(expected, int | float) and expected:
        return (measured - expected) / expected
    return None


def _check(c, metric, expected, measured, tolerance, passed) -> dict[str, Any]:
    return {
        "concurrency": c,
        "metric": metric,
        "expected": expected,
        "measured": measured,
        "rel_error": _rel_error(expected, measured),
        "tolerance": tolerance,
        "passed": passed,
    }


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _wait_healthy(base_url: str, proc: asyncio.subprocess.Process, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    async with aiohttp.ClientSession() as session:
        while time.monotonic() < deadline:
            if proc.returncode is not None:
                stderr = (await proc.stderr.read()).decode(errors="replace")
                raise RuntimeError(f"mock server exited early:\n{stderr}")
            try:
                async with session.get(f"{base_url}/health") as resp:
                    if resp.status == 200:
                        return
            except aiohttp.ClientError:
                pass
            await asyncio.sleep(0.1)
    raise TimeoutError(f"mock server not healthy after {timeout_s}s")


async def _http_rtt_ms(base_url: str, samples: int = 50) -> float:
    """Median localhost round trip of a request that does no engine work."""
    times = []
    async with aiohttp.ClientSession() as session:
        for _ in range(samples + 5):  # the first few warm the connection up
            t = time.perf_counter()
            async with session.get(f"{base_url}/health") as resp:
                await resp.read()
            times.append((time.perf_counter() - t) * 1e3)
    return statistics.median(times[5:])


async def run_smoke(out_dir: Path, quiet: bool = False) -> tuple[Path, list[dict[str, Any]]]:
    """Start the mock in a subprocess (so it does not share the client's event loop), sweep it,
    check the results against the analytic expectations and plot them."""
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "bench",
        "mock",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        *SMOKE_MOCK.to_cli_args(),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        await _wait_healthy(base_url, proc, timeout_s=30)
        http_rtt_ms = await _http_rtt_ms(base_url)
        cfg = RunConfig(
            base_url=base_url,
            target="mock",
            model=SMOKE_MOCK.model,
            concurrency=SMOKE_LEVELS,
            requests_per_level=32,
            prompt_len="uniform:50,150",
            output_len="uniform:16,32",
            slo_ttft_ms=50.0,
            slo_tpot_ms=15.0,
            pause_between_levels_s=0.5,
            out_dir=out_dir,
            notes="Load-generator validation against the CPU mock server (python -m bench smoke).",
            quiet=quiet,
        )
        run_dir = await run_benchmark(cfg)
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except TimeoutError:
                proc.kill()
                await proc.wait()

    checks = validate(load_run(run_dir).rows, SMOKE_MOCK, http_rtt_ms)
    write_csv(run_dir / "validation.csv", checks, VALIDATION_COLUMNS)
    plot_runs([run_dir], run_dir / "plots")
    return run_dir, checks


def format_checks(checks: list[dict[str, Any]]) -> str:
    def num(v: Any) -> str:
        return "-" if not isinstance(v, int | float) else f"{v:,.2f}"

    lines = [f"{'c':>4}  {'metric':<18} {'expected':>10} {'measured':>10} {'error':>8}  result"]
    for ch in checks:
        err = "-" if ch["rel_error"] is None else f"{ch['rel_error']:+.1%}"
        result = "info" if ch["tolerance"] == "info" else ("PASS" if ch["passed"] else "FAIL")
        c = "-" if ch["concurrency"] is None else ch["concurrency"]
        lines.append(
            f"{c:>4}  {ch['metric']:<18} {num(ch['expected']):>10} "
            f"{num(ch['measured']):>10} {err:>8}  {result}"
        )
    return "\n".join(lines)
