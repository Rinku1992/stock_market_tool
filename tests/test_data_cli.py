from datetime import date, datetime, timezone

import pyarrow as pa

from smt.data.lake import Lake
from smt.data_cli import find_gaps

UTC = timezone.utc


def _row(d, close):
    return {"instrument_id": "X", "exchange": "NSE", "trade_date": d, "open": close, "high": close, "low": close,
            "close": close, "volume": 1, "price_basis": "as_traded", "source": "t", "source_ref": None,
            "published_at": datetime(d.year, d.month, d.day, 10, tzinfo=UTC),
            "ingested_at": datetime(2026, 11, 12, tzinfo=UTC), "raw_sha256": "a", "schema_version": 2}


def test_gaps_finds_an_unadjusted_bonus_drop(tmp_path):
    lake = Lake(tmp_path)
    rows = [_row(date(2024, 10, 24), 2700.0), _row(date(2024, 10, 25), 2650.0), _row(date(2024, 10, 28), 1330.0)]
    lake.write("prices_daily", pa.Table.from_pylist(rows))
    assert find_gaps(lake, "X") == [(date(2024, 10, 28), 2650.0, 1330.0, -49.8)]
    assert find_gaps(lake, "X", pct=60) == []
