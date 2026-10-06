"""Command line: ``python -m bench {run,mock,smoke,analyze,plot,cost}``."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from bench.client import API_PATHS


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {value}")
    return value


def _nonnegative_int(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0, got {value}")
    return value


def _levels(text: str) -> list[int]:
    try:
        levels = [int(x) for x in text.split(",") if x.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"expected comma-separated integers, got {text!r}"
        ) from None
    if not levels or min(levels) < 1:
        raise argparse.ArgumentTypeError("concurrency levels must be integers >= 1")
    if len(set(levels)) != len(levels):
        raise argparse.ArgumentTypeError("concurrency levels must not repeat")
    return levels


def _json_object(text: str) -> dict:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"invalid JSON: {exc}") from None
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("must be a JSON object")
    return value


def _length_spec(text: str) -> str:
    from bench.workload import LengthDist

    try:
        LengthDist.parse(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None
    return text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bench", description="LLM serving benchmark toolkit.")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Concurrency sweep against an OpenAI-compatible server.")
    run.add_argument(
        "--base-url",
        default=os.environ.get("BENCH_BASE_URL"),
        help="Server root, e.g. http://localhost:8000 (env BENCH_BASE_URL).",
    )
    run.add_argument(
        "--target",
        default=os.environ.get("BENCH_TARGET"),
        help="Label for this setup, e.g. vllm-gke (env BENCH_TARGET).",
    )
    run.add_argument(
        "--model",
        default=os.environ.get("MODEL_ID"),
        help="Model name sent in requests (env MODEL_ID).",
    )
    run.add_argument("--api", choices=sorted(API_PATHS), default="completions")
    run.add_argument("--path", help="Request path override (default: per --api).")
    run.add_argument(
        "--concurrency",
        type=_levels,
        default=[1, 2, 4, 8, 16, 32, 64],
        help="Comma-separated levels (default: 1,2,4,8,16,32,64).",
    )
    run.add_argument(
        "--requests-per-level",
        type=_positive_int,
        default=100,
        help="Measured requests per level (at least 4 x concurrency).",
    )
    run.add_argument(
        "--warmup-requests",
        type=_nonnegative_int,
        help="Warm-up requests per level (default: the concurrency).",
    )
    run.add_argument(
        "--prompt-len",
        type=_length_spec,
        default="uniform:200,800",
        help="Prompt words: fixed:N | uniform:LO,HI | normal:MEAN,STD[,MIN,MAX].",
    )
    run.add_argument(
        "--output-len",
        type=_length_spec,
        default="uniform:128,384",
        help="max_tokens per request, same syntax.",
    )
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--temperature", type=float, default=0.0)
    run.add_argument(
        "--extra-body",
        type=_json_object,
        default={},
        help="JSON merged into each request body, e.g. '{\"ignore_eos\": true}'.",
    )
    run.add_argument(
        "--header",
        action="append",
        default=[],
        metavar="'NAME: VALUE'",
        help="Extra request header (repeatable). Only names are saved.",
    )
    run.add_argument(
        "--bearer-token-env",
        metavar="VAR",
        help="Send 'Authorization: Bearer $VAR'. The token is never saved.",
    )
    run.add_argument(
        "--no-stream-usage",
        action="store_true",
        help="Do not request stream_options.include_usage.",
    )
    run.add_argument("--timeout-s", type=float, default=600.0, help="Per-request timeout.")
    run.add_argument("--slo-ttft-ms", type=float, help="TTFT SLO for slo_attainment.")
    run.add_argument("--slo-tpot-ms", type=float, help="TPOT SLO for slo_attainment.")
    run.add_argument("--pause-s", type=float, default=2.0, help="Pause between levels.")
    run.add_argument(
        "--ramp-up-s", type=float, default=0.0, help="Stagger worker start over this many seconds."
    )
    run.add_argument("--out-dir", type=Path, default=Path("results"))
    run.add_argument("--run-id", help="Default: <UTC timestamp>_<target>.")
    run.add_argument("--notes", default="", help="Free text saved in meta.json.")
    run.add_argument("--quiet", action="store_true")
    run.set_defaults(func=_cmd_run)

    mock = sub.add_parser("mock", help="Run the CPU-only mock OpenAI-compatible server.")
    mock.add_argument("--host", default="127.0.0.1")
    mock.add_argument("--port", type=int, default=8000)
    mock.add_argument("--access-log", action="store_true")
    from bench.mock_server import add_mock_arguments

    add_mock_arguments(mock)
    mock.set_defaults(func=_cmd_mock)

    smoke = sub.add_parser("smoke", help="Validate the load generator against the mock.")
    smoke.add_argument("--out-dir", type=Path, default=Path("results/validation"))
    smoke.add_argument("--quiet", action="store_true")
    smoke.set_defaults(func=_cmd_smoke)

    analyze = sub.add_parser("analyze", help="Recompute summary/requests CSVs from raw records.")
    analyze.add_argument("run_dirs", nargs="+", type=Path)
    analyze.set_defaults(func=_cmd_analyze)

    plot = sub.add_parser("plot", help="Plot TTFT, ITL and throughput for one or more runs.")
    plot.add_argument("run_dirs", nargs="+", type=Path)
    plot.add_argument("--out", type=Path, default=Path("results/report"))
    plot.add_argument("--themes", default="light,dark")
    plot.set_defaults(func=_cmd_plot)

    cost = sub.add_parser("cost", help="$ per 1M output tokens from runs and confirmed prices.")
    cost.add_argument("run_dirs", nargs="+", type=Path)
    cost.add_argument("--prices", type=Path, default=Path("bench/prices.toml"))
    cost.add_argument("--out", type=Path, default=Path("results/report"))
    cost.add_argument("--slo-min-attainment", type=float, default=0.95)
    cost.set_defaults(func=_cmd_cost)
    return parser


def _cmd_run(args: argparse.Namespace) -> int:
    from bench.runner import RunConfig, run_benchmark

    for name, env in (
        ("base_url", "BENCH_BASE_URL"),
        ("target", "BENCH_TARGET"),
        ("model", "MODEL_ID"),
    ):
        if not getattr(args, name):
            raise SystemExit(f"error: --{name.replace('_', '-')} (or ${env}) is required")
    headers = {}
    for header in args.header:
        name, sep, value = header.partition(":")
        if not sep or not name.strip():
            raise SystemExit(f"error: --header must look like 'Name: value', got {header!r}")
        headers[name.strip()] = value.strip()
    if args.bearer_token_env:
        token = os.environ.get(args.bearer_token_env)
        if not token:
            raise SystemExit(f"error: environment variable {args.bearer_token_env} is empty")
        headers["Authorization"] = f"Bearer {token}"

    cfg = RunConfig(
        base_url=args.base_url,
        target=args.target,
        model=args.model,
        concurrency=args.concurrency,
        api=args.api,
        path=args.path,
        requests_per_level=args.requests_per_level,
        warmup_requests=args.warmup_requests,
        prompt_len=args.prompt_len,
        output_len=args.output_len,
        seed=args.seed,
        temperature=args.temperature,
        extra_body=args.extra_body,
        headers=headers,
        stream_usage=not args.no_stream_usage,
        timeout_s=args.timeout_s,
        slo_ttft_ms=args.slo_ttft_ms,
        slo_tpot_ms=args.slo_tpot_ms,
        pause_between_levels_s=args.pause_s,
        ramp_up_s=args.ramp_up_s,
        out_dir=args.out_dir,
        run_id=args.run_id,
        notes=args.notes,
        quiet=args.quiet,
    )
    run_dir = asyncio.run(run_benchmark(cfg))
    print(run_dir)
    return 0


def _cmd_mock(args: argparse.Namespace) -> int:
    from bench.mock_server import mock_config_from_args, serve

    serve(mock_config_from_args(args), args.host, args.port, access_log=args.access_log)
    return 0


def _cmd_smoke(args: argparse.Namespace) -> int:
    from bench.smoke import format_checks, run_smoke

    run_dir, checks = asyncio.run(run_smoke(args.out_dir, quiet=args.quiet))
    print(format_checks(checks))
    failed = [c for c in checks if not c["passed"]]
    verdict = f"FAILED ({len(failed)} of {len(checks)} checks)" if failed else "PASSED"
    print(f"\nvalidation {verdict}; results in {run_dir}")
    return 1 if failed else 0


def _cmd_analyze(args: argparse.Namespace) -> int:
    from bench.report import analyze_run

    for run_dir in args.run_dirs:
        rows = analyze_run(run_dir)
        print(f"{run_dir}: {len(rows)} levels")
    return 0


def _cmd_plot(args: argparse.Namespace) -> int:
    from bench.plots import plot_runs

    themes = [t.strip() for t in args.themes.split(",") if t.strip()]
    for path in plot_runs(args.run_dirs, args.out, themes):
        print(path)
    return 0


def _cmd_cost(args: argparse.Namespace) -> int:
    from bench.cost import best_rows, cost_rows, load_prices, write_cost_report
    from bench.report import load_run, workload_mismatch

    runs = [load_run(d) for d in args.run_dirs]
    if workload_mismatch(runs):
        print("WARNING: runs used different workloads; they are not comparable.", file=sys.stderr)
    prices = load_prices(args.prices)
    rows = cost_rows(runs, prices)
    for status in sorted({r["price_status"] for r in rows} - {"confirmed"}):
        targets = sorted({r["target"] for r in rows if r["price_status"] == status})
        print(f"note: {status} price for {', '.join(targets)}; cost left empty.", file=sys.stderr)
    best = best_rows(rows, args.slo_min_attainment)
    for path in write_cost_report(rows, best, prices, args.out):
        print(path)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
