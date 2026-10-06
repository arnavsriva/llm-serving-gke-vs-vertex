import textwrap

import pytest

from bench.cost import best_rows, cost_rows, load_prices, usd_per_million_tokens, write_cost_report
from bench.report import Run

PRICES = textwrap.dedent(
    """
    [targets.a]
    description = "two confirmed items"
    [[targets.a.items]]
    name = "node"
    usd_per_hour = 0.75
    source = "https://example.invalid/pricing"
    as_of = "2026-10-01"
    confirmed = true
    [[targets.a.items]]
    name = "overhead"
    usd_per_hour = 0.25
    confirmed = true

    [targets.b]
    description = "placeholder"
    [[targets.b.items]]
    name = "node"
    usd_per_hour = 0.0
    confirmed = false
    """
)


def run(target, rows):
    return Run(path=None, meta={"target": target}, rows=rows)


def test_formula():
    # $1/h at 1000 tok/s: 3.6M tokens per hour -> $0.2778 per 1M.
    assert usd_per_million_tokens(1.0, 1000.0) == pytest.approx(1 / 3.6)
    assert usd_per_million_tokens(1.0, 0.0) is None
    assert usd_per_million_tokens(1.0, None) is None


def test_only_confirmed_prices_produce_costs(tmp_path):
    path = tmp_path / "prices.toml"
    path.write_text(PRICES)
    prices = load_prices(path)
    assert prices["a"].usd_per_hour == pytest.approx(1.0) and prices["a"].confirmed
    assert not prices["b"].confirmed

    runs = [
        run("a", [{"concurrency": 1, "output_tok_s": 500.0}]),
        run("b", [{"concurrency": 1, "output_tok_s": 500.0}]),
        run("c", [{"concurrency": 1, "output_tok_s": 500.0}]),
    ]
    rows = {r["target"]: r for r in cost_rows(runs, prices)}
    assert rows["a"]["usd_per_1m_output_tokens"] == pytest.approx(1 / 1.8)
    assert rows["b"]["usd_per_1m_output_tokens"] is None
    assert rows["b"]["price_status"] == "unconfirmed"
    assert rows["c"]["price_status"] == "missing"

    best = best_rows(list(rows.values()), 0.95)
    csv_path, md_path = write_cost_report(list(rows.values()), best, prices, tmp_path / "out")
    assert csv_path.exists()
    md = md_path.read_text()
    assert "$0.556" in md and "unconfirmed price" in md and "NOT confirmed" in md


def test_best_rows_pick_peak_and_slo_compliant_levels():
    rows = [
        {"target": "a", "concurrency": 1, "output_tok_s": 100.0, "slo_attainment": 1.0},
        {"target": "a", "concurrency": 8, "output_tok_s": 600.0, "slo_attainment": 0.97},
        {"target": "a", "concurrency": 32, "output_tok_s": 900.0, "slo_attainment": 0.40},
    ]
    best = {r["basis"]: r["concurrency"] for r in best_rows(rows, 0.95)}
    assert best == {"peak throughput": 32, "best with SLO attainment >= 95%": 8}
