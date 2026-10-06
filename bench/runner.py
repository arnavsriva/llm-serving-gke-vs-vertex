"""Closed-loop concurrency sweep against one OpenAI-compatible endpoint.

At concurrency ``c`` there are ``c`` workers, each keeping exactly one request in flight. The
first requests issued at a level are warm-up, the next ``n`` are measured, and any issued after
that are cool-down: they hold the load at ``c`` until the last measured request has finished and
are then cancelled. Each level draws prompts from its own fixed block of workload indices, so the
measured prompts are identical on every target, however the timing turns out.
"""

from __future__ import annotations

import asyncio
import os
import platform
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from bench.client import Endpoint, endpoint_url, send_request
from bench.metrics import summarize_level
from bench.records import RequestRecord
from bench.report import META_FILE, analyze_run, write_json, write_raw
from bench.workload import LengthDist, Workload

LEVEL_INDEX_STRIDE = 1_000_000
MIN_MEASURED_PER_WORKER = 4


@dataclass
class RunConfig:
    base_url: str
    target: str
    model: str
    concurrency: list[int]
    api: str = "completions"
    path: str | None = None
    requests_per_level: int = 100
    warmup_requests: int | None = None
    prompt_len: str = "uniform:200,800"
    output_len: str = "uniform:128,384"
    seed: int = 0
    temperature: float = 0.0
    extra_body: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    stream_usage: bool = True
    timeout_s: float = 600.0
    slo_ttft_ms: float | None = None
    slo_tpot_ms: float | None = None
    pause_between_levels_s: float = 2.0
    ramp_up_s: float = 0.0
    out_dir: Path = Path("results")
    run_id: str | None = None
    notes: str = ""
    quiet: bool = False

    def level_counts(self, concurrency: int) -> tuple[int, int]:
        """(warm-up, measured) request counts for one level."""
        warmup = concurrency if self.warmup_requests is None else self.warmup_requests
        measured = max(self.requests_per_level, MIN_MEASURED_PER_WORKER * concurrency)
        return warmup, measured


class LoopLagMonitor:
    """Samples how late the event loop wakes up; large lag means the client is the bottleneck."""

    def __init__(self, interval_s: float = 0.01) -> None:
        self.interval_s = interval_s
        self.samples_ms: list[float] = []
        self._task: asyncio.Task | None = None

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            t = loop.time()
            await asyncio.sleep(self.interval_s)
            self.samples_ms.append((loop.time() - t - self.interval_s) * 1e3)

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()


async def run_level(
    session: aiohttp.ClientSession,
    endpoint: Endpoint,
    workload: Workload,
    level_idx: int,
    concurrency: int,
    n_warmup: int,
    n_measured: int,
    now: Any,
    ramp_up_s: float = 0.0,
) -> tuple[list[RequestRecord], list[float], int]:
    """Run one level; return (records, loop-lag samples in ms, number of requests issued)."""
    base = level_idx * LEVEL_INDEX_STRIDE
    records: list[RequestRecord] = []
    issued = 0
    finished_measured = 0
    measured_done = asyncio.Event()

    async def worker(wid: int) -> None:
        nonlocal issued, finished_measured
        if ramp_up_s > 0:
            await asyncio.sleep(ramp_up_s * wid / concurrency)
        while not measured_done.is_set():
            k = issued
            issued += 1
            if k < n_warmup:
                phase = "warmup"
            elif k < n_warmup + n_measured:
                phase = "measured"
            else:
                phase = "cooldown"
            spec = workload.request(base + k)
            record = RequestRecord(
                index=spec.index,
                concurrency=concurrency,
                phase=phase,
                prompt_words=spec.prompt_words,
                max_tokens=spec.max_tokens,
            )
            records.append(record)
            await send_request(session, endpoint, spec, record, now)
            if phase == "measured":
                finished_measured += 1
                if finished_measured == n_measured:
                    measured_done.set()

    lag = LoopLagMonitor()
    lag.start()
    tasks = [asyncio.create_task(worker(i)) for i in range(concurrency)]
    done_waiter = asyncio.create_task(measured_done.wait())
    try:
        while not measured_done.is_set():
            done, _ = await asyncio.wait({done_waiter, *tasks}, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task is not done_waiter and task.exception() is not None:
                    raise task.exception()
    finally:
        for task in (*tasks, done_waiter):
            task.cancel()
        await asyncio.gather(*tasks, done_waiter, return_exceptions=True)
        lag.stop()
    return records, lag.samples_ms, issued


async def run_benchmark(cfg: RunConfig) -> Path:
    """Run the full sweep and write ``results/<run_id>/``; return that directory."""
    workload = Workload(
        LengthDist.parse(cfg.prompt_len), LengthDist.parse(cfg.output_len), cfg.seed
    )
    endpoint = Endpoint(
        url=endpoint_url(cfg.base_url, cfg.api, cfg.path),
        api=cfg.api,
        model=cfg.model,
        headers=cfg.headers,
        temperature=cfg.temperature,
        stream_usage=cfg.stream_usage,
        extra_body=cfg.extra_body,
    )
    run_id = cfg.run_id or make_run_id(cfg.target)
    run_dir = Path(cfg.out_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    meta = _build_meta(cfg, run_id, workload, endpoint)
    write_json(run_dir / META_FILE, meta)

    t0 = time.perf_counter()

    def now() -> float:
        return time.perf_counter() - t0

    records: list[RequestRecord] = []
    lag_by_level: dict[int, list[float]] = {}
    timeout = aiohttp.ClientTimeout(total=cfg.timeout_s, sock_connect=30)
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=0), timeout=timeout
    ) as session:
        for level_idx, c in enumerate(cfg.concurrency):
            n_warmup, n_measured = cfg.level_counts(c)
            started = time.perf_counter()
            level_records, lag, issued = await run_level(
                session, endpoint, workload, level_idx, c, n_warmup, n_measured, now, cfg.ramp_up_s
            )
            records += level_records
            lag_by_level[c] = lag
            meta["levels"].append(
                {
                    "concurrency": c,
                    "warmup": n_warmup,
                    "measured": n_measured,
                    "cooldown": issued - n_warmup - n_measured,
                }
            )
            # Persist after every level so an interrupted (and paid-for) sweep is not lost.
            write_json(run_dir / META_FILE, meta)
            write_raw(run_dir, records, lag_by_level)
            if not cfg.quiet:
                row = summarize_level(
                    level_records, c, cfg.target, cfg.slo_ttft_ms, cfg.slo_tpot_ms, lag
                )
                _print_level(row, time.perf_counter() - started)
            if level_idx < len(cfg.concurrency) - 1:
                await asyncio.sleep(cfg.pause_between_levels_s)

    meta["finished_at_utc"] = _utc_now()
    write_json(run_dir / META_FILE, meta)
    analyze_run(run_dir)
    return run_dir


def make_run_id(target: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", target.lower()).strip("-") or "run"
    return f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}_{slug}"


def redact_url(url: str) -> str:
    """Drop credentials and query string, and mask GCP project IDs in the path."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    path = re.sub(r"(/projects/)[^/]+", r"\1REDACTED", parts.path)
    return urlunsplit((parts.scheme, host, path, "", ""))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _git_info() -> dict[str, Any]:
    root = Path(__file__).resolve().parent.parent

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, timeout=5, check=True
        ).stdout.strip()

    try:
        commit = git("log", "-1", "--format=%H")
        dirty = bool(git("status", "--porcelain", "--untracked-files=no"))
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}
    return {"commit": commit or None, "dirty": dirty}


def _build_meta(
    cfg: RunConfig, run_id: str, workload: Workload, endpoint: Endpoint
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "target": cfg.target,
        "model": cfg.model,
        "api": cfg.api,
        "url": redact_url(endpoint.url),
        "started_at_utc": _utc_now(),
        "finished_at_utc": None,
        "git": _git_info(),
        "workload": {**workload.describe(), "fingerprint": workload.fingerprint()},
        "concurrency": list(cfg.concurrency),
        "requests_per_level": cfg.requests_per_level,
        "warmup_requests": cfg.warmup_requests,
        "sampling": {"temperature": cfg.temperature, "stream_usage": cfg.stream_usage},
        "extra_body": cfg.extra_body,
        "header_names": sorted(cfg.headers),
        "slo": {"ttft_ms": cfg.slo_ttft_ms, "tpot_ms": cfg.slo_tpot_ms},
        "client": {
            "python": platform.python_version(),
            "aiohttp": aiohttp.__version__,
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
        },
        "notes": cfg.notes,
        "levels": [],
    }


def _print_level(row: dict[str, Any], elapsed_s: float) -> None:
    def fmt(key: str, spec: str) -> str:
        value = row.get(key)
        return "-" if value is None else format(value, spec)

    print(
        f"[{row['target']}] c={row['concurrency']:>4}  ok {row['n_ok']}/{row['n_measured']}  "
        f"out {fmt('output_tok_s', ',.1f')} tok/s  TTFT p50 {fmt('ttft_ms_p50', ',.1f')} ms  "
        f"TPOT p50 {fmt('tpot_ms_p50', ',.2f')} ms  ({elapsed_s:.1f}s)",
        file=sys.stderr,
        flush=True,
    )
