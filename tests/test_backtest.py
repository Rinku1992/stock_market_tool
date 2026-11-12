from datetime import date, datetime, timedelta, timezone

import pyarrow as pa
import pytest

from smt.analysts.market_activity import BENCHMARK, price_structure
from smt.backtest import FWD_DAYS, month_ends, run_backtest, summarize
from smt.data.lake import Lake
from smt.data.prices import load_bars
from smt.schema import AnalystKind, AnalystReport, Direction, Evidence, Horizon, HorizonView

UTC = timezone.utc
START = date(2022, 1, 1)
N = 1100


def _write(lake, instrument_id, closes):
    rows = []
    for i, c in enumerate(closes):
        d = START + timedelta(days=i)
        rows.append({"instrument_id": instrument_id, "exchange": "NSE", "trade_date": d, "price_basis": "adjusted",
                     "open": c, "high": c, "low": c, "close": c, "volume": 1000, "source": "t", "source_ref": None,
                     "published_at": datetime(d.year, d.month, d.day, 10, tzinfo=UTC),
                     "ingested_at": datetime(2026, 11, 12, tzinfo=UTC), "raw_sha256": instrument_id, "schema_version": 2})
    lake.write("prices_daily", pa.Table.from_pylist(rows))


def _lake(tmp_path, stocks: dict[str, float], index_rate=0.0003):
    lake = Lake(tmp_path)
    for iid, rate in stocks.items():
        _write(lake, iid, [100 * (1 + rate) ** i for i in range(N)])
    _write(lake, BENCHMARK, [100 * (1 + index_rate) ** i for i in range(N)])
    return lake


def test_month_ends_skips_the_unfinished_last_month():
    ds = [date(2026, 1, 30), date(2026, 1, 31), date(2026, 2, 1), date(2026, 2, 27), date(2026, 3, 2), date(2026, 3, 3)]
    assert month_ends(ds) == [date(2026, 1, 31), date(2026, 2, 27)]


def test_steady_winner_and_loser_are_scored_correctly(tmp_path):
    lake = _lake(tmp_path, {"UP": 0.002, "DOWN": -0.002})
    rows = {(r.horizon, r.view): r for r in summarize(run_backtest(lake, ["UP", "DOWN"], price_structure))}
    pos, neg = rows[(Horizon.SHORT, "positive")], rows[(Horizon.SHORT, "negative")]
    assert pos.hit_rate == 1.0 and pos.mean_excess > 0 and pos.n_stocks == 1
    assert neg.hit_rate == 1.0 and neg.mean_excess < 0
    assert rows[(Horizon.SHORT, "ALL")].n == pos.n + neg.n
    assert rows[(Horizon.LONG, "neutral")].hit_rate is None  # price trend gives no 2-year view


def test_forward_return_maths(tmp_path):
    lake = _lake(tmp_path, {"UP": 0.002})
    o = next(x for x in run_backtest(lake, ["UP"], price_structure) if x.horizon == Horizon.SHORT)
    h = FWD_DAYS[Horizon.SHORT]
    assert o.fwd_return == pytest.approx(1.002 ** h - 1)
    assert o.fwd_index_return == pytest.approx(1.0003 ** h - 1)
    assert o.excess == pytest.approx(o.fwd_return - o.fwd_index_return)


def test_analyst_never_sees_the_future(tmp_path):
    lake = _lake(tmp_path, {"UP": 0.002})
    seen = []

    def spy(ctx):
        bars = load_bars(ctx.lake, ctx.instrument_id, ctx.as_of)
        seen.append((ctx.as_of, bars[-1].d))
        return AnalystReport(
            analyst_id="spy", unit="t", kind=AnalystKind.CODE, symbol=ctx.symbol, as_of=ctx.as_of, finding="x",
            evidence=[Evidence(id="e", source_type="price", ref="r", as_of=bars[-1].d)],
            views=[HorizonView(horizon=h, direction=Direction.NEUTRAL, strength=0, confidence=0) for h in Horizon])

    run_backtest(lake, ["UP"], spy)
    assert seen and all(last_bar <= as_of for as_of, last_bar in seen)


def test_views_without_enough_future_are_not_scored(tmp_path):
    lake = _lake(tmp_path, {"UP": 0.002})
    obs = run_backtest(lake, ["UP"], price_structure)
    last_day = START + timedelta(days=N - 1)
    for o in obs:
        assert o.as_of + timedelta(days=FWD_DAYS[o.horizon]) <= last_day


def test_missing_benchmark_is_an_error(tmp_path):
    lake = Lake(tmp_path)
    _write(lake, "UP", [100.0] * 300)
    with pytest.raises(ValueError, match="benchmark"):
        run_backtest(lake, ["UP"], price_structure)
