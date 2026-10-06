"""Pure functions that turn request records into per-level metrics.

Latency metrics (TTFT, TPOT, ITL, E2E) use *measured* requests only. Each of them ran while
exactly ``concurrency`` requests were in flight: warm-up requests precede them, and cool-down
requests keep the load on until the last one finishes. Throughput counts the generated tokens of
*all* requests whose chunks arrived inside the measurement window, which runs from the first
measured start to the last measured finish, so the ramp-up and drain never dilute it.
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from typing import Any

import numpy as np

from bench.records import RequestRecord

PERCENTILES = (50, 90, 95, 99)

SUMMARY_COLUMNS = (
    "target",
    "concurrency",
    "n_measured",
    "n_ok",
    "n_errors",
    "window_s",
    "output_tok_s",
    "requests_s",
    "ttft_ms_mean",
    "ttft_ms_p50",
    "ttft_ms_p90",
    "ttft_ms_p95",
    "ttft_ms_p99",
    "tpot_ms_mean",
    "tpot_ms_p50",
    "tpot_ms_p90",
    "tpot_ms_p95",
    "tpot_ms_p99",
    "itl_ms_p50",
    "itl_ms_p90",
    "itl_ms_p95",
    "itl_ms_p99",
    "e2e_s_mean",
    "e2e_s_p50",
    "e2e_s_p95",
    "e2e_s_p99",
    "prompt_tokens_mean",
    "output_tokens_mean",
    "token_source",
    "slo_attainment",
    "littles_law_ratio",
    "client_lag_ms_p99",
    "client_lag_ms_max",
)

REQUEST_COLUMNS = (
    "target",
    "concurrency",
    "index",
    "ok",
    "error",
    "http_status",
    "t_start_s",
    "ttft_ms",
    "tpot_ms",
    "e2e_ms",
    "itl_p50_ms",
    "itl_max_ms",
    "prompt_words",
    "prompt_tokens",
    "max_tokens",
    "output_tokens",
    "token_source",
    "num_chunks",
    "finish_reason",
)


def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear-interpolated percentile (numpy default); ``None`` for no data."""
    if len(values) == 0:
        return None
    return float(np.percentile(values, q))


def mean(values: Sequence[float]) -> float | None:
    if len(values) == 0:
        return None
    return float(np.mean(values))


def _scaled(value: float | None, scale: float) -> float | None:
    return None if value is None else value * scale


def _dist(
    prefix: str,
    values: Sequence[float],
    scale: float,
    pcts: Sequence[int] = PERCENTILES,
    with_mean: bool = True,
) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    if with_mean:
        out[f"{prefix}_mean"] = _scaled(mean(values), scale)
    for q in pcts:
        out[f"{prefix}_p{q}"] = _scaled(percentile(values, q), scale)
    return out


def measurement_window(records: Sequence[RequestRecord]) -> tuple[float, float] | None:
    """[first measured start, last measured end], or ``None`` if nothing was measured."""
    measured = [
        r
        for r in records
        if r.phase == "measured" and r.t_start is not None and r.t_end is not None
    ]
    if not measured:
        return None
    return min(r.t_start for r in measured), max(r.t_end for r in measured)


def tokens_per_chunk(records: Sequence[RequestRecord]) -> float:
    """Average tokens per text chunk over completed requests with usage data (default 1.0)."""
    with_usage = [r for r in records if r.ok and r.completion_tokens is not None and r.chunk_times]
    chunks = sum(len(r.chunk_times) for r in with_usage)
    if chunks == 0:
        return 1.0
    return sum(r.completion_tokens for r in with_usage) / chunks


def windowed_output_tokens(records: Sequence[RequestRecord], start: float, end: float) -> float:
    """Generated tokens, from any request, whose chunks arrived within ``[start, end]``.

    A chunk is weighted by its own request's tokens-per-chunk when the server reported usage,
    otherwise by the level average (cancelled or failed requests have no usage).
    """
    fallback = tokens_per_chunk(records)
    total = 0.0
    for r in records:
        if not r.chunk_times:
            continue
        n = bisect.bisect_right(r.chunk_times, end) - bisect.bisect_left(r.chunk_times, start)
        if n == 0:
            continue
        if r.ok and r.completion_tokens is not None:
            total += n * r.completion_tokens / len(r.chunk_times)
        else:
            total += n * fallback
    return total


def meets_slo(r: RequestRecord, slo_ttft_ms: float | None, slo_tpot_ms: float | None) -> bool:
    """A failed request never meets the SLO; a missing TPOT (one-token output) is not checked."""
    if not r.ok:
        return False
    if slo_ttft_ms is not None and r.ttft * 1000 > slo_ttft_ms:
        return False
    return not (slo_tpot_ms is not None and r.tpot is not None and r.tpot * 1000 > slo_tpot_ms)


def summarize_level(
    records: Sequence[RequestRecord],
    concurrency: int,
    target: str,
    slo_ttft_ms: float | None = None,
    slo_tpot_ms: float | None = None,
    lag_ms: Sequence[float] = (),
) -> dict[str, Any]:
    """One row of ``summary.csv`` for one concurrency level (keys follow ``SUMMARY_COLUMNS``)."""
    measured = [r for r in records if r.phase == "measured"]
    ok = [r for r in measured if r.ok]
    row: dict[str, Any] = {
        "target": target,
        "concurrency": concurrency,
        "n_measured": len(measured),
        "n_ok": len(ok),
        "n_errors": len(measured) - len(ok),
    }

    window = measurement_window(records)
    if window is not None and window[1] > window[0]:
        start, end = window
        duration = end - start
        row["window_s"] = duration
        row["output_tok_s"] = windowed_output_tokens(records, start, end) / duration

    e2e = [r.e2e for r in ok]
    row |= _dist("ttft_ms", [r.ttft for r in ok], 1000.0)
    row |= _dist("tpot_ms", [r.tpot for r in ok if r.tpot is not None], 1000.0)
    row |= _dist("itl_ms", [g for r in ok for g in r.itls], 1000.0, with_mean=False)
    row |= _dist("e2e_s", e2e, 1.0, pcts=(50, 95, 99))
    row["prompt_tokens_mean"] = mean([r.prompt_tokens for r in ok if r.prompt_tokens is not None])
    row["output_tokens_mean"] = mean([r.output_tokens for r in ok])
    sources = {r.token_source for r in ok}
    row["token_source"] = sources.pop() if len(sources) == 1 else ("mixed" if sources else None)

    if measured and (slo_ttft_ms is not None or slo_tpot_ms is not None):
        met = sum(meets_slo(r, slo_ttft_ms, slo_tpot_ms) for r in measured)
        row["slo_attainment"] = met / len(measured)
    # Request rate is derived from token throughput rather than by counting completions in the
    # window: the window's edges are themselves completions, often simultaneous ones, so a direct
    # count is off by a whole batch at small sample sizes.
    if row.get("output_tok_s") and row["output_tokens_mean"]:
        row["requests_s"] = row["output_tok_s"] / row["output_tokens_mean"]
        # Little's law for a closed loop: requests/s x mean latency = requests in flight. Well
        # below 1 means the client did not keep `concurrency` requests in flight.
        row["littles_law_ratio"] = row["requests_s"] * mean(e2e) / concurrency
    if len(lag_ms) > 0:
        row["client_lag_ms_p99"] = percentile(lag_ms, 99)
        row["client_lag_ms_max"] = max(lag_ms)
    return row


def request_rows(records: Sequence[RequestRecord], target: str) -> list[dict[str, Any]]:
    """Rows of ``requests.csv``: one per measured request (keys follow ``REQUEST_COLUMNS``)."""
    rows = []
    for r in records:
        if r.phase != "measured":
            continue
        itls = r.itls
        rows.append(
            {
                "target": target,
                "concurrency": r.concurrency,
                "index": r.index,
                "ok": r.ok,
                "error": r.error,
                "http_status": r.http_status,
                "t_start_s": r.t_start,
                "ttft_ms": _scaled(r.ttft, 1000.0),
                "tpot_ms": _scaled(r.tpot, 1000.0),
                "e2e_ms": _scaled(r.e2e, 1000.0),
                "itl_p50_ms": _scaled(percentile(itls, 50), 1000.0),
                "itl_max_ms": _scaled(max(itls), 1000.0) if itls else None,
                "prompt_words": r.prompt_words,
                "prompt_tokens": r.prompt_tokens,
                "max_tokens": r.max_tokens,
                "output_tokens": r.output_tokens,
                "token_source": r.token_source,
                "num_chunks": len(r.chunk_times),
                "finish_reason": r.finish_reason,
            }
        )
    return rows
