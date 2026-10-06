"""Run-directory I/O: raw records, ``summary.csv`` / ``requests.csv``, and loading saved runs.

Layout of ``results/<run_id>/``::

    meta.json             run configuration, workload fingerprint, git commit, client info
    summary.csv           one row per concurrency level (the numbers that get reported)
    requests.csv          one row per measured request
    raw/records.jsonl.gz  every request incl. chunk timestamps (gitignored; input to `analyze`)
    raw/loop_lag.json     client event-loop lag samples per level (gitignored)
"""

from __future__ import annotations

import csv
import gzip
import json
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bench.metrics import REQUEST_COLUMNS, SUMMARY_COLUMNS, request_rows, summarize_level
from bench.records import RequestRecord

META_FILE = "meta.json"
SUMMARY_FILE = "summary.csv"
REQUESTS_FILE = "requests.csv"
RAW_DIR = "raw"
RECORDS_FILE = "records.jsonl.gz"
LAG_FILE = "loop_lag.json"


@dataclass(frozen=True)
class Run:
    path: Path
    meta: dict[str, Any]
    rows: list[dict[str, Any]]

    @property
    def target(self) -> str:
        return self.meta["target"]


def write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2) + "\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def write_raw(
    run_dir: Path, records: Iterable[RequestRecord], lag_by_level: Mapping[int, Sequence[float]]
) -> None:
    raw = run_dir / RAW_DIR
    raw.mkdir(parents=True, exist_ok=True)
    with gzip.open(raw / RECORDS_FILE, "wt", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r.to_dict()) + "\n")
    write_json(
        raw / LAG_FILE, {str(c): [round(x, 4) for x in lag] for c, lag in lag_by_level.items()}
    )


def read_raw(run_dir: Path) -> tuple[list[RequestRecord], dict[int, list[float]]]:
    raw = run_dir / RAW_DIR
    with gzip.open(raw / RECORDS_FILE, "rt", encoding="utf-8") as f:
        records = [RequestRecord.from_dict(json.loads(line)) for line in f if line.strip()]
    lag_path = raw / LAG_FILE
    lag = {int(c): v for c, v in read_json(lag_path).items()} if lag_path.exists() else {}
    return records, lag


def analyze_run(run_dir: Path) -> list[dict[str, Any]]:
    """(Re)compute ``summary.csv`` and ``requests.csv`` from the raw records of a run."""
    meta = read_json(run_dir / META_FILE)
    records, lag = read_raw(run_dir)
    target = meta["target"]
    slo = meta.get("slo") or {}
    by_level: dict[int, list[RequestRecord]] = defaultdict(list)
    for r in records:
        by_level[r.concurrency].append(r)
    levels = [lv["concurrency"] for lv in meta.get("levels", [])] or sorted(by_level)
    rows = [
        summarize_level(
            by_level[c], c, target, slo.get("ttft_ms"), slo.get("tpot_ms"), lag.get(c, ())
        )
        for c in levels
    ]
    write_csv(run_dir / SUMMARY_FILE, rows, SUMMARY_COLUMNS)
    write_csv(run_dir / REQUESTS_FILE, request_rows(records, target), REQUEST_COLUMNS)
    return rows


def load_run(run_dir: Path) -> Run:
    run_dir = Path(run_dir)
    return Run(run_dir, read_json(run_dir / META_FILE), read_csv(run_dir / SUMMARY_FILE))


def write_csv(path: Path, rows: Iterable[Mapping[str, Any]], columns: Sequence[str]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: _format(row.get(c)) for c in columns})


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="") as f:
        return [{k: _parse(v) for k, v in row.items()} for row in csv.DictReader(f)]


def _format(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.6g}" if math.isfinite(value) else ""
    return value


def _parse(value: str) -> Any:
    if value == "":
        return None
    if value in ("true", "false"):
        return value == "true"
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            pass
    return value
