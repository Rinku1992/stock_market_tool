"""Calibration officer, on small made-up backtest results."""

import json
import random
from datetime import date

from smt.backtest import Obs
from smt.calibrate import assess, calibrate, describe_span, edge, scored_span, period_edges, regime_by_date, weights_doc, write_weights
from smt.data.prices import Bar
from smt.schema import Direction, Horizon

DIRS = [Direction.POSITIVE, Direction.NEGATIVE, Direction.NEUTRAL]
DATES = [date(2020 + m // 12, m % 12 + 1, 28) for m in range(36)]


def _obs(effect, n_stocks=40, seed=1, h=Horizon.MID):
    """effect(date_index) -> extra excess for positive views (minus for negative)."""
    rng, out = random.Random(seed), []
    for di, d in enumerate(DATES):
        for s in range(n_stocks):
            v = rng.choice(DIRS)
            sign = {Direction.POSITIVE: 1, Direction.NEGATIVE: -1, Direction.NEUTRAL: 0}[v]
            out.append(Obs(f"S{s}", d, h, v, sign * effect(di) + rng.gauss(0, 0.05), 0.0))
    return out


def test_steady_edge_gets_a_weight():
    c = assess(_obs(lambda i: 0.02))  # 4-point gap between positive and negative views, every period
    assert c.n_pos >= 300 and c.n_neg >= 300 and c.stable and c.beat_share >= 0.95
    assert 0 < c.weight < 1 and c.weight == round(c.edge / (c.edge + 0.02), 3)
    assert c.why.startswith("Edge +")


def test_random_analyst_gets_zero():
    c = assess(_obs(lambda i: 0.0, seed=2))
    assert c.weight == 0 and "Weight 0" in c.why


def test_edge_in_only_one_period_gets_zero():
    c = assess(_obs(lambda i: 0.06 if i < 12 else 0.0, seed=3))
    assert c.edge > 0 and not c.stable and c.weight == 0
    assert "not above 0 in all 3 periods" in c.why


def test_too_few_cases_gets_zero():
    c = assess(_obs(lambda i: 0.02, n_stocks=10))  # about 120 of each view
    assert c.weight == 0 and "too few cases" in c.why


def test_context_only_analyst():
    obs = [Obs("S", d, Horizon.MID, Direction.NEUTRAL, 0.01, 0.0) for d in DATES]
    c = assess(obs)
    assert c.weight == 0 and c.edge is None and "no direction" in c.why


def test_edge_and_periods():
    obs = [Obs("A", DATES[i], Horizon.MID, Direction.POSITIVE, 0.03, 0.0) for i in range(3)]
    obs += [Obs("B", DATES[i], Horizon.MID, Direction.NEGATIVE, -0.01, 0.0) for i in range(3)]
    assert abs(edge(obs) - 0.04) < 1e-12
    assert [round(x, 4) for x in period_edges(obs)] == [0.04, 0.04, 0.04]


def test_regime_is_reported_but_not_used():
    bars = [Bar(date.fromordinal(date(2020, 1, 1).toordinal() + i), 100 + i, 0) for i in range(250)]
    reg = regime_by_date(bars)
    assert bars[198].d not in reg and reg[bars[199].d] == "above"
    obs = _obs(lambda i: 0.02)
    with_reg = assess(obs, {d: "above" for d in DATES[:18]} | {d: "below" for d in DATES[18:]})
    assert with_reg.weight == assess(obs).weight
    assert with_reg.regime["above"][0] + with_reg.regime["below"][0] == len(obs)


def test_write_keeps_the_old_version(tmp_path):
    (tmp_path / "market_activity.json").write_text(json.dumps({"unit": "market_activity", "version": "2026-11-13"}))
    analysts = {"good": lambda: _obs(lambda i: 0.02), "flat": lambda: []}
    results = calibrate(analysts, lambda fn: fn(), {})
    assert results["good"][Horizon.MID].weight > 0 and results["flat"][Horizon.MID].weight == 0
    doc = weights_doc("market_activity", results, "2026-11-14", "test")
    write_weights("market_activity", doc, tmp_path)
    write_weights("market_activity", doc, tmp_path)  # a second write never overwrites history
    new = json.loads((tmp_path / "market_activity.json").read_text())
    assert set(new["weights"]) == {"good", "flat"}
    assert set(new["weights"]["good"]) == {"short", "mid", "long"} and new["why"]["good"]
    hist = sorted(p.name for p in (tmp_path / "history").iterdir())
    assert hist == ["market_activity_2026-11-13.json", "market_activity_2026-11-14.json"]
    assert new["version"] == "2026-11-14.2"  # the second write on the same day gets its own name


def test_same_day_version_gets_a_new_name(tmp_path):
    (tmp_path / "market_activity.json").write_text(json.dumps({"unit": "market_activity", "version": "2026-11-13"}))
    write_weights("market_activity", {"unit": "market_activity", "version": "2026-11-13", "weights": {}}, tmp_path)
    assert json.loads((tmp_path / "market_activity.json").read_text())["version"] == "2026-11-13.2"
    assert json.loads((tmp_path / "history" / "market_activity_2026-11-13.json").read_text())["version"] == "2026-11-13"


def test_span_counts_only_scored_directional_views_per_horizon():
    obs = [Obs("A", date(2022, 2, 28), Horizon.SHORT, Direction.NEUTRAL, 0.0, 0.0),  # neutral: not in the edge
           Obs("A", date(2022, 9, 30), Horizon.SHORT, Direction.POSITIVE, 0.0, 0.0),
           Obs("A", date(2026, 6, 30), Horizon.SHORT, Direction.NEGATIVE, 0.0, 0.0),
           Obs("A", date(2026, 1, 30), Horizon.MID, Direction.POSITIVE, 0.0, 0.0),
           Obs("A", date(2024, 10, 31), Horizon.LONG, Direction.NEUTRAL, 0.0, 0.0)]
    span = scored_span(obs)
    assert span == {Horizon.SHORT: (date(2022, 9, 30), date(2026, 6, 30)),
                    Horizon.MID: (date(2026, 1, 30), date(2026, 1, 30)), Horizon.LONG: None}
    assert describe_span(span) == "short 2022-09-30 to 2026-06-30, mid 2026-01-30 to 2026-01-30, long none"
