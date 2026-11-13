import math
from datetime import date, datetime, timedelta, timezone

import pyarrow as pa
import pytest

from smt.analysts.base import AnalystContext
from smt.analysts.market_activity import BENCHMARK, momentum
from smt.backtest import run_backtest, summarize
from smt.data.lake import Lake
from smt.factors import momentum_12_1, universe_scores
from smt.schema import Direction, Horizon
from smt.store import FeatureStore

UTC = timezone.utc
START = date(2022, 1, 1)
N = 800


def _write(lake, iid, closes, start=START):
    rows = []
    for k, c in enumerate(closes):
        d = start + timedelta(days=k)
        rows.append({"instrument_id": iid, "exchange": "NSE", "trade_date": d, "price_basis": "adjusted",
                     "open": c, "high": c, "low": c, "close": c, "volume": 1000, "source": "t", "source_ref": None,
                     "published_at": datetime(d.year, d.month, d.day, 10, tzinfo=UTC),
                     "ingested_at": datetime(2026, 11, 12, tzinfo=UTC), "raw_sha256": iid, "schema_version": 2})
    lake.write("prices_daily", pa.Table.from_pylist(rows))


def _series(rate, seed, n=N):
    # steady trend plus small wiggles, so volatility is not zero
    return [100 * (1 + rate) ** k * (1 + 0.01 * math.sin(k * 0.7 + seed)) for k in range(n)]


def _lake(tmp_path, n_stocks=12):
    lake = Lake(tmp_path)
    for i in range(n_stocks):
        _write(lake, f"S{i:02d}", _series(-0.001 + i * 0.00025, i))
    _write(lake, BENCHMARK, _series(0.0003, 99))
    return lake


def _ctx(lake, iid, as_of=START + timedelta(days=N - 1)):
    return AnalystContext(symbol=iid, as_of=as_of, store=FeatureStore(), lake=lake, instrument_id=iid)


def _dir(r, h):
    return next(v for v in r.views if v.horizon == h).direction


def test_momentum_skips_the_last_month():
    closes = [100 * 1.001 ** k for k in range(300)]
    assert momentum_12_1(closes) == pytest.approx(1.001 ** 231 - 1)
    assert momentum_12_1(closes[:252]) is None


def test_top_third_positive_bottom_third_negative(tmp_path):
    lake = _lake(tmp_path)
    best, worst, middle = momentum(_ctx(lake, "S11")), momentum(_ctx(lake, "S00")), momentum(_ctx(lake, "S06"))
    assert _dir(best, Horizon.SHORT) == Direction.POSITIVE and _dir(best, Horizon.MID) == Direction.POSITIVE
    assert _dir(worst, Horizon.SHORT) == Direction.NEGATIVE
    assert _dir(middle, Horizon.SHORT) == Direction.NEUTRAL
    assert _dir(best, Horizon.LONG) == Direction.NEUTRAL
    assert "Rank 1 of 12" in best.finding and "Rank 12 of 12" in worst.finding
    assert all(v.confidence <= 0.3 for v in best.views)


def test_small_universe_gives_no_view(tmp_path):
    lake = _lake(tmp_path, n_stocks=5)
    r = momentum(_ctx(lake, "S04"))
    assert all(v.direction == Direction.NEUTRAL for v in r.views)
    assert any("only 5 stocks" in n for n in r.could_not_check)


def test_short_or_late_history_is_left_out(tmp_path):
    lake = _lake(tmp_path)
    _write(lake, "NEW", _series(0.002, 3, n=100), start=START + timedelta(days=N - 100))
    as_of = START + timedelta(days=N - 1)
    assert "NEW" not in universe_scores(lake, as_of)
    assert "not enough recent price history" in momentum(_ctx(lake, "NEW")).could_not_check[0]


def test_stale_stock_is_left_out(tmp_path):
    lake = _lake(tmp_path)
    _write(lake, "HALTED", _series(0.001, 4, n=N - 30))  # last price 30 days before as_of
    assert "HALTED" not in universe_scores(lake, START + timedelta(days=N - 1))


def test_backtest_on_persistent_trends(tmp_path):
    lake = _lake(tmp_path)
    ids = [f"S{i:02d}" for i in range(12)]
    rows = {(r.horizon, r.view): r for r in summarize(run_backtest(lake, ids, momentum))}
    assert rows[(Horizon.SHORT, "positive")].mean_excess > 0 > rows[(Horizon.SHORT, "negative")].mean_excess
    assert rows[(Horizon.SHORT, "positive")].median_excess > 0


def test_universe_benchmark_centres_each_date_on_zero(tmp_path):
    from statistics import median as med
    lake = _lake(tmp_path)
    ids = [f"S{i:02d}" for i in range(12)]
    obs = run_backtest(lake, ids, momentum, vs="universe")
    by_slot = {}
    for o in obs:
        by_slot.setdefault((o.as_of, o.horizon), []).append(o.excess)
    assert by_slot and all(abs(med(v)) < 1e-12 for v in by_slot.values() if len(v) == 12)
    rows = {(r.horizon, r.view): r for r in summarize(obs)}
    assert rows[(Horizon.MID, "positive")].median_excess > 0 > rows[(Horizon.MID, "negative")].median_excess


def test_universe_benchmark_needs_ten_stocks(tmp_path):
    lake = _lake(tmp_path, n_stocks=5)
    assert run_backtest(lake, [f"S{i:02d}" for i in range(5)], momentum, vs="universe") == []
