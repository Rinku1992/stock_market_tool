import math
from datetime import date, datetime, timedelta, timezone

import pyarrow as pa
import pytest

from smt import indicators as ind
from smt.analysts.base import AnalystContext
from smt.analysts.market_activity import BENCHMARK, liquidity, market_beta, price_structure
from smt.data.lake import Lake
from smt.graph import run
from smt.schema import Direction, Horizon
from smt.store import FeatureStore

UTC = timezone.utc
START = date(2025, 1, 1)
N = 320
AS_OF = START + timedelta(days=N - 1)
STOCK = "INE000TEST01"


def _write(lake, instrument_id, closes, volume=1_000_000):
    rows = []
    for i, c in enumerate(closes):
        d = START + timedelta(days=i)
        rows.append({"instrument_id": instrument_id, "exchange": "NSE", "trade_date": d, "price_basis": "adjusted",
                     "open": c, "high": c, "low": c, "close": c, "volume": volume, "source": "t", "source_ref": None,
                     "published_at": datetime(d.year, d.month, d.day, 10, tzinfo=UTC),
                     "ingested_at": datetime(2026, 11, 12, tzinfo=UTC), "raw_sha256": instrument_id, "schema_version": 2})
    lake.write("prices_daily", pa.Table.from_pylist(rows))


def _ctx(lake, as_of=AS_OF):
    return AnalystContext(symbol="TEST", as_of=as_of, store=FeatureStore(), lake=lake, instrument_id=STOCK)


def _dir(report, h):
    return next(v for v in report.views if v.horizon == h).direction


def _up(rate):
    return [100 * (1 + rate) ** i for i in range(N)]


def test_indicators_basic():
    xs = [float(i) for i in range(1, 11)]
    assert ind.sma(xs, 5) == 8.0 and ind.sma(xs, 11) is None
    assert ind.pct_change(xs, 5) == pytest.approx(1.0)
    assert ind.dist_from_high([10.0, 20.0, 15.0], 3) == pytest.approx(-0.25)


def test_uptrend_beating_index_is_positive(tmp_path):
    lake = Lake(tmp_path)
    _write(lake, STOCK, _up(0.003))
    _write(lake, BENCHMARK, _up(0.0005))
    ctx = _ctx(lake)
    r = price_structure(ctx)
    assert _dir(r, Horizon.SHORT) == Direction.POSITIVE and _dir(r, Horizon.MID) == Direction.POSITIVE
    assert _dir(r, Horizon.LONG) == Direction.NEUTRAL
    assert all(v.confidence <= 0.3 for v in r.views) and r.evidence
    assert any("not backtested" in n for n in r.could_not_check)
    assert ctx.store.get_feature_asof(STOCK, "sma_200", AS_OF)["origin"] == "code"


def test_downtrend_lagging_index_is_negative(tmp_path):
    lake = Lake(tmp_path)
    _write(lake, STOCK, _up(-0.003))
    _write(lake, BENCHMARK, _up(0.0005))
    r = price_structure(_ctx(lake))
    assert _dir(r, Horizon.SHORT) == Direction.NEGATIVE and _dir(r, Horizon.MID) == Direction.NEGATIVE


def test_uptrend_that_lags_index_is_neutral(tmp_path):
    lake = Lake(tmp_path)
    _write(lake, STOCK, _up(0.001))
    _write(lake, BENCHMARK, _up(0.003))
    assert _dir(price_structure(_ctx(lake)), Horizon.SHORT) == Direction.NEUTRAL


def test_missing_index_is_noted_not_hidden(tmp_path):
    lake = Lake(tmp_path)
    _write(lake, STOCK, _up(0.003))
    r = price_structure(_ctx(lake))
    assert any("no index prices" in n for n in r.could_not_check)
    assert "no index prices" in market_beta(_ctx(lake)).finding


def test_too_little_history_gives_no_view(tmp_path):
    lake = Lake(tmp_path)
    _write(lake, STOCK, _up(0.003))
    r = price_structure(_ctx(lake, as_of=START + timedelta(days=50)))  # point-in-time: later rows must be ignored
    assert "Only 51 daily prices" in r.finding and _dir(r, Horizon.SHORT) == Direction.NEUTRAL


def test_no_lake_is_handled():
    r = price_structure(AnalystContext(symbol="T", as_of=AS_OF, store=FeatureStore()))
    assert "Only 0" in r.finding


def test_liquidity_reports_traded_value(tmp_path):
    lake = Lake(tmp_path)
    _write(lake, STOCK, [100.0] * N, volume=2_000_000)  # 100 * 2,000,000 = Rs 20 crore a day
    assert "Rs 20 crore" in liquidity(_ctx(lake)).finding


def test_beta_of_a_stock_that_moves_twice_the_index(tmp_path):
    lake = Lake(tmp_path)
    idx, stock = [100.0], [100.0]
    for i in range(1, N):
        r = 0.01 * math.sin(i)
        idx.append(idx[-1] * (1 + r))
        stock.append(stock[-1] * (1 + 2 * r))
    _write(lake, STOCK, stock)
    _write(lake, BENCHMARK, idx)
    ctx = _ctx(lake)
    assert "beta to Nifty 50: 2.00" in market_beta(ctx).finding
    assert ctx.store.get_feature_asof(STOCK, "r2_1y", AS_OF)["value"] == pytest.approx(1.0)


def test_graph_runs_real_analysts_with_a_lake(tmp_path):
    lake = Lake(tmp_path)
    _write(lake, STOCK, _up(0.003))
    _write(lake, BENCHMARK, _up(0.0005))
    state = run("test", AS_OF, FeatureStore(), lake=lake, instrument_id=STOCK)
    found = {r.analyst_id: r for r in state["reports"]}
    assert {"price_structure", "liquidity", "market_beta"} <= set(found)
    assert _dir(found["price_structure"], Horizon.SHORT) == Direction.POSITIVE
