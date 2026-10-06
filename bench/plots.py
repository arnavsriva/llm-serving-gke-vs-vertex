"""Comparison plots: TTFT, inter-token latency and output throughput against concurrency.

One series per target, coloured by a fixed target -> slot mapping so a target keeps its colour
across every chart. Latency charts are small multiples (p50 | p95) on a shared y-axis instead of
two line styles on one plot. Each chart is rendered for a light and a dark surface.

Palette: the dataviz reference categorical slots 1-3 (blue, orange, aqua), which pass the
colour-vision-deficiency checks pairwise in both modes. Light-mode aqua is under 3:1 contrast, so
series also get distinct markers and direct end labels, and every value is in summary.csv.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import matplotlib
from matplotlib import font_manager
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.ticker import FixedLocator, FuncFormatter, LogLocator, NullFormatter, NullLocator

from bench.report import Run, load_run, workload_mismatch

THEMES = {
    "light": {
        "surface": "#fcfcfb",
        "ink": "#0b0b0b",
        "ink2": "#52514e",
        "muted": "#898781",
        "grid": "#e1e0d9",
        "axis": "#c3c2b7",
        "series": (
            "#2a78d6",
            "#eb6834",
            "#1baf7a",
            "#eda100",
            "#e87ba4",
            "#008300",
            "#4a3aa7",
            "#e34948",
        ),
    },
    "dark": {
        "surface": "#1a1a19",
        "ink": "#ffffff",
        "ink2": "#c3c2b7",
        "muted": "#898781",
        "grid": "#2c2c2a",
        "axis": "#383835",
        "series": (
            "#3987e5",
            "#d95926",
            "#199e70",
            "#c98500",
            "#d55181",
            "#008300",
            "#9085e9",
            "#e66767",
        ),
    },
}
KNOWN_TARGETS = ("vllm-gke", "sglang-gke", "vllm-vertex")
MARKERS = ("o", "s", "^", "D", "v", "P", "X", "h")
FONTS = ("Inter", "Helvetica Neue", "Helvetica", "Arial", "Liberation Sans", "DejaVu Sans")

# Sizes in points for a 200-dpi PNG shown at about half size (1 CSS px ~ 0.72 pt).
LINE_PT, MARKER_PT, RING_PT, HAIRLINE_PT = 1.44, 5.8, 1.44, 0.72
TEXT_PT, TITLE_PT = 8.5, 11.5
DPI = 200


@dataclass(frozen=True)
class ChartSpec:
    name: str
    title: str
    ylabel: str
    columns: tuple[str, ...]
    panel_titles: tuple[str, ...] = ()
    allow_log: bool = False


CHARTS = (
    ChartSpec(
        "ttft_vs_concurrency",
        "Time to first token",
        "TTFT (ms)",
        ("ttft_ms_p50", "ttft_ms_p95"),
        ("p50", "p95"),
        allow_log=True,
    ),
    ChartSpec(
        "itl_vs_concurrency",
        "Inter-token latency",
        "ITL (ms)",
        ("itl_ms_p50", "itl_ms_p95"),
        ("p50", "p95"),
    ),
    ChartSpec(
        "throughput_vs_concurrency",
        "Output throughput",
        "Output tokens / s",
        ("output_tok_s",),
    ),
)


def assign_slots(targets: Sequence[str]) -> dict[str, int]:
    """Known targets own fixed slots; others take the lowest free slots in order of appearance."""
    slots = {t: KNOWN_TARGETS.index(t) for t in targets if t in KNOWN_TARGETS}
    free = [i for i in range(len(MARKERS)) if i not in slots.values()]
    for t in targets:
        if t not in slots:
            if not free:
                raise ValueError("more than 8 targets in one chart; split them across charts")
            slots[t] = free.pop(0)
    return slots


def plot_runs(
    run_dirs: Sequence[Path], out_dir: Path, themes: Sequence[str] = ("light", "dark")
) -> list[Path]:
    runs = [load_run(Path(d)) for d in run_dirs]
    targets = [run.target for run in runs]
    if len(set(targets)) != len(targets):
        raise ValueError(f"each run must have a distinct target label, got {targets}")
    if workload_mismatch(runs):
        print("WARNING: runs used different workloads; they are not comparable.", file=sys.stderr)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    slots = assign_slots(targets)
    paths = []
    with matplotlib.rc_context({"font.family": _font_family(), "font.size": TEXT_PT}):
        for spec in CHARTS:
            for theme in themes:
                path = out_dir / f"{spec.name}_{theme}.png"
                _draw(spec, runs, slots, THEMES[theme], path)
                paths.append(path)
    return paths


def _font_family() -> str:
    available = {f.name for f in font_manager.fontManager.ttflist}
    return next((f for f in FONTS if f in available), "DejaVu Sans")


def _series(run: Run, column: str) -> tuple[list[float], list[float]]:
    xs, ys = [], []
    for row in sorted(run.rows, key=lambda r: r["concurrency"]):
        value = row.get(column)
        xs.append(float(row["concurrency"]))
        ys.append(float(value) if isinstance(value, int | float) else math.nan)
    return xs, ys


def _subtitle(runs: Sequence[Run]) -> str:
    meta = runs[0].meta
    workload = meta.get("workload", {})
    text = (
        f"{meta.get('model', '?')}, prompt {workload.get('prompt_len_words', '?')} words, "
        f"output {workload.get('output_len_tokens', '?')} tokens, {meta.get('api', '?')} API, "
        "closed loop"
    )
    if workload_mismatch(runs):
        text += "  |  WARNING: workloads differ, not comparable"
    return text


def _tick_label(value: float, _pos: int | None = None) -> str:
    """Thousands separators, no trailing zeros, and never rounded (12.5 must not read "12")."""
    text = f"{value:,.2f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _style_axes(ax, theme: dict) -> None:
    ax.set_facecolor(theme["surface"])
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(theme["axis"])
    ax.spines["bottom"].set_linewidth(HAIRLINE_PT)
    ax.grid(axis="y", color=theme["grid"], linewidth=HAIRLINE_PT, linestyle="-")
    ax.set_axisbelow(True)
    ax.tick_params(which="both", length=0, labelsize=TEXT_PT, labelcolor=theme["ink2"], pad=4)


def _draw(spec: ChartSpec, runs: Sequence[Run], slots: dict[str, int], theme: dict, path: Path):
    multi = len(runs) > 1
    fig = Figure(figsize=(8.0, 3.9 if multi else 3.6), dpi=DPI, facecolor=theme["surface"])
    canvas = FigureCanvasAgg(fig)
    axes = fig.subplots(1, len(spec.columns), sharey=True, squeeze=False)[0]
    levels = sorted({float(r["concurrency"]) for run in runs for r in run.rows})
    positive = []
    ends: list[tuple[str, float, float]] = []  # (target, x, y) of each series' last point

    for i, (ax, column) in enumerate(zip(axes, spec.columns, strict=True)):
        _style_axes(ax, theme)
        for run in runs:
            slot = slots[run.target]
            xs, ys = _series(run, column)
            ax.plot(
                xs,
                ys,
                color=theme["series"][slot],
                linewidth=LINE_PT,
                marker=MARKERS[slot],
                markersize=MARKER_PT,
                markeredgecolor=theme["surface"],
                markeredgewidth=RING_PT,
                solid_capstyle="round",
                solid_joinstyle="round",
                zorder=3,
                clip_on=False,
            )
            positive += [y for y in ys if math.isfinite(y) and y > 0]
            if i == len(axes) - 1:
                last = [(x, y) for x, y in zip(xs, ys, strict=True) if math.isfinite(y)]
                if last:
                    ends.append((run.target, *last[-1]))
        if spec.panel_titles:
            ax.set_title(
                spec.panel_titles[i], loc="left", color=theme["ink2"], fontsize=TEXT_PT, pad=6
            )
        ax.set_xscale("log", base=2)
        ax.xaxis.set_major_locator(FixedLocator(levels))
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
        ax.xaxis.set_minor_locator(NullLocator())
        ax.set_xlabel("Concurrent requests", color=theme["ink2"], fontsize=TEXT_PT)

    log_y = spec.allow_log and positive and max(positive) / min(positive) > 50
    for ax in axes:
        if log_y:
            ax.set_yscale("log")
            ax.yaxis.set_major_locator(LogLocator(base=10, subs=(1.0, 2.0, 5.0)))
            ax.yaxis.set_minor_locator(NullLocator())
            ax.yaxis.set_minor_formatter(NullFormatter())
        else:
            ax.set_ylim(bottom=0)
        ax.yaxis.set_major_formatter(FuncFormatter(_tick_label))
    ylabel = spec.ylabel + (", log scale" if log_y else "")
    axes[0].set_ylabel(ylabel, color=theme["ink2"], fontsize=TEXT_PT)

    title = spec.title if multi else f"{spec.title}: {runs[0].target}"
    fig.text(
        0.07,
        0.965,
        title,
        ha="left",
        va="top",
        fontsize=TITLE_PT,
        weight="bold",
        color=theme["ink"],
    )
    fig.text(
        0.07, 0.895, _subtitle(runs), ha="left", va="top", fontsize=TEXT_PT, color=theme["ink2"]
    )
    top = 0.78 if spec.panel_titles else 0.80
    if multi:
        handles = [
            Line2D(
                [],
                [],
                color=theme["series"][slots[run.target]],
                linewidth=LINE_PT,
                marker=MARKERS[slots[run.target]],
                markersize=MARKER_PT,
                markeredgewidth=0,
            )
            for run in runs
        ]
        fig.legend(
            handles,
            [run.target for run in runs],
            loc="upper left",
            bbox_to_anchor=(0.062, 0.86),
            ncols=len(runs),
            frameon=False,
            fontsize=TEXT_PT,
            labelcolor=theme["ink2"],
            handlelength=1.8,
            columnspacing=1.8,
        )
        top = 0.74
    fig.subplots_adjust(left=0.09, right=0.86 if multi else 0.97, top=top, bottom=0.15, wspace=0.1)

    if multi and len(runs) <= 4:
        canvas.draw()
        _direct_labels(axes[-1], ends, theme)
    fig.savefig(path, dpi=DPI, facecolor=theme["surface"])


def _direct_labels(ax, ends: list[tuple[str, float, float]], theme: dict) -> None:
    """Label each series at its last point, unless labels would collide (the legend remains)."""
    if not ends:
        return
    ys = sorted(ax.transData.transform((x, y))[1] for _, x, y in ends)
    min_gap_px = TEXT_PT * 1.5 * DPI / 72
    if any(b - a < min_gap_px for a, b in pairwise(ys)):
        return
    for target, x, y in ends:
        ax.annotate(
            target,
            (x, y),
            xytext=(9, 0),
            textcoords="offset points",
            va="center",
            ha="left",
            fontsize=TEXT_PT,
            color=theme["ink2"],
            annotation_clip=False,
        )
