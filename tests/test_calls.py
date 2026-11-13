"""Call logger: today only, after the close, no duplicates, current prices, contract rows. Made-up data."""

from datetime import date, datetime, timedelta, timezone

import pyarrow as pa
import pytest

from smt.analysts.market_activity import BENCHMARK, liquidity, price_structure
from smt.calls import IST, CallRecord, RecordRefused, call_rows, check_record_time, record_day, status
from smt.data.contract import CALLS
from smt.data.lake import Lake
from smt.data.validate import ContractError, conform
from smt.graph import run
from smt.kite_cli import update_targets, update_window
from smt.record import print_status
from smt.store import FeatureStore

UTC = timezone.utc
TODAY = date(2026, 11, 13)
AFTER_CLOSE = datetime(2026, 11, 13, 16, 0, tzinfo=IST)
A, B = "INE000AAAA01", "INE000BBBB01"
STOCKS = [(A, "AAA"), (B, "BBB")]


def _prices(lake, iid, last=TODAY, n=320, rate=0.001):
    rows = []
    for i in range(n):
        d = last - timedelta(days=n - 1 - i)
        c = 100 * (1 + rate) ** i
        rows.append({"instrument_id": iid, "exchange": "NSE", "trade_date": d, "price_basis": "adjusted",
                     "open": c, "high": c, "low": c, "close": c, "volume": 1_000_000, "source": "t", "source_ref": None,
                     "published_at": datetime(d.year, d.month, d.day, 10, tzinfo=UTC),
                     "ingested_at": datetime(2026, 11, 13, 11, tzinfo=UTC), "raw_sha256": iid, "schema_version": 2})
    lake.write("prices_daily", pa.Table.from_pylist(rows), iid)


def _lake(tmp_path, b_last=TODAY, index_last=TODAY):
    lake = Lake(tmp_path / "lake")
    _prices(lake, BENCHMARK, index_last, rate=0.0005)
    _prices(lake, A)
    _prices(lake, B, b_last, rate=-0.001)
    return lake


def _run(symbol, as_of, store, **kw):  # two quick analysts keep the test fast
    return run(symbol, as_of, store, desks={"market_activity": [price_structure, liquidity]}, **kw)


def _record(lake, **kw):
    return record_day(lake, FeatureStore(), STOCKS, run_fn=_run, version="abc1234", **{"now": AFTER_CLOSE, **kw})


def _count(lake):
    return lake.query(f"SELECT count(*) FROM read_parquet('{(lake.canonical / 'calls').as_posix()}/**/*.parquet')")[0][0]


def test_past_date_is_refused(tmp_path):
    lake = _lake(tmp_path)
    with pytest.raises(RecordRefused, match="No backfill"):
        _record(lake, as_of=date(2026, 11, 12))
    assert not lake.has("calls")


def test_before_the_close_is_refused():
    with pytest.raises(RecordRefused, match="after the market close at 15:30"):
        check_record_time(TODAY, datetime(2026, 11, 13, 15, 29, tzinfo=IST))
    check_record_time(TODAY, datetime(2026, 11, 13, 15, 30, tzinfo=IST))  # 15:30 itself is fine
    # the date is India's date: 19:00 UTC on the 13th is already the 14th in India, so the 13th is refused
    with pytest.raises(RecordRefused, match="only be recorded for today \\(2026-11-14"):
        check_record_time(TODAY, datetime(2026, 11, 13, 19, 0, tzinfo=UTC))


def test_recording_twice_adds_nothing(tmp_path):
    lake = _lake(tmp_path)
    first = _record(lake)
    assert first.recorded == ["AAA", "BBB"] and first.rows > 0
    n = _count(lake)
    second = _record(lake, now=AFTER_CLOSE + timedelta(hours=1))
    assert second.recorded == [] and second.already == ["AAA", "BBB"] and _count(lake) == n


def test_same_stock_in_holdings_and_index_is_recorded_once(tmp_path):
    lake = _lake(tmp_path)
    res = record_day(lake, FeatureStore(), STOCKS + [(A, "AAA")], run_fn=_run, version="v", now=AFTER_CLOSE)
    assert res.recorded == ["AAA", "BBB"]


def test_stale_prices_stop_the_run(tmp_path):
    lake = _lake(tmp_path, b_last=TODAY - timedelta(days=1))
    with pytest.raises(RecordRefused, match=r"BBB \(latest 2026-11-12\).*Nothing was recorded"):
        _record(lake)
    assert not lake.has("calls")


def test_no_index_bar_today_means_closed_or_not_updated(tmp_path):
    lake = _lake(tmp_path, index_last=TODAY - timedelta(days=1))
    with pytest.raises(RecordRefused, match="market was closed today, or prices are not updated"):
        _record(lake)


def test_rows_pass_the_contract_and_cover_every_source(tmp_path):
    lake = _lake(tmp_path)
    _record(lake)
    rows = lake.query(f"SELECT source_kind, source_id, horizon, direction, call, close_price, weights_version, code_version, "
                      f"symbol FROM {lake.read_sql('calls')} WHERE instrument_id = ?", [A])
    kinds = {(r[0], r[1]) for r in rows}
    assert kinds == {("analyst", "price_structure"), ("analyst", "liquidity"), ("desk", "market_activity"), ("verdict", "verdict")}
    assert len(rows) == 4 * 3  # each source, each horizon
    assert all(r[2] in {"short", "mid", "long"} and r[7] == "abc1234" and r[8] == "AAA" for r in rows)
    assert {r[4] for r in rows if r[0] == "verdict"} == {"no_call"} and all(r[3] == "neutral" for r in rows if r[0] == "verdict")
    assert all(r[4] is None for r in rows if r[0] != "verdict")
    assert all(r[6] for r in rows if r[0] == "desk") and all(r[6] is None for r in rows if r[0] == "analyst")
    assert rows[0][5] == pytest.approx(100 * 1.001 ** 319)  # close on the day


def test_contract_rejects_a_bad_row(tmp_path):
    lake = _lake(tmp_path)
    state = _run("AAA", TODAY, FeatureStore(), lake=lake, instrument_id=A)
    rows = call_rows(state, A, 123.0, "v", datetime(2026, 11, 13, 11, tzinfo=UTC))
    full = [{**r, "source": "call_logger", "source_ref": None, "ingested_at": datetime(2026, 11, 13, 12, tzinfo=UTC),
             "raw_sha256": "x", "schema_version": 1} for r in rows]
    conform(pa.Table.from_pylist(full), CALLS)  # good rows pass
    with pytest.raises(ContractError, match="direction"):
        conform(pa.Table.from_pylist([{**full[0], "direction": "up"}] + full[1:]), CALLS)
    with pytest.raises(ContractError, match="duplicate"):
        conform(pa.Table.from_pylist(full + full[:1]), CALLS)
    assert CallRecord(rows).parse(CallRecord(rows).fetch())[0]["as_of"] == TODAY  # raw round trip


def test_status_counts_calls_per_day(tmp_path, capsys):
    lake = _lake(tmp_path)
    print_status(lake)
    assert "No calls recorded yet." in capsys.readouterr().out
    _record(lake)
    [(d, stocks, calls, *_rest, ver)] = status(lake)
    assert (d, stocks, calls, ver) == (TODAY, 2, 24, "abc1234")
    print_status(lake)
    assert "2026-11-13" in capsys.readouterr().out


def test_update_prices_targets_and_window(tmp_path):
    lake = Lake(tmp_path / "lake")
    lin = {"source": "t", "published_at": datetime(2026, 1, 1, tzinfo=UTC), "ingested_at": datetime(2026, 1, 2, tzinfo=UTC),
           "raw_sha256": "x", "schema_version": 1}
    lake.write("index_members", pa.Table.from_pylist(
        [{"index_id": "IDX:NIFTY100", "instrument_id": i, "valid_from": date(2026, 1, 1), "valid_to": None, "source_ref": s, **lin}
         for i, s in STOCKS]))
    lake.write("holdings", pa.Table.from_pylist(
        [{"account": "main", "instrument_id": i, "as_of": date(2026, 1, 1), "quantity": 1.0, "avg_price": 1.0, "source_ref": s, **lin}
         for i, s in [(A, "AAA"), ("INE000CCCC01", "CCC")]]))
    assert update_targets(lake) == [("IDX:NIFTY50", "NIFTY 50"), (A, "AAA"), (B, "BBB"), ("INE000CCCC01", "CCC")]
    assert update_window(datetime(2026, 11, 13, 15, 29, tzinfo=IST)) == (date(2026, 11, 2), date(2026, 11, 12))
    assert update_window(datetime(2026, 11, 13, 15, 30, tzinfo=IST)) == (date(2026, 11, 3), date(2026, 11, 13))
