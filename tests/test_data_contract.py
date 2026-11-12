import csv
import io
from datetime import date, datetime, timezone

import pyarrow as pa
import pytest

from smt.data.adapter import ingest, reparse
from smt.data.contract import DATASETS, FINANCIALS, PRICES_DAILY
from smt.data.lake import Lake
from smt.data.validate import ContractError, conform

UTC = timezone.utc
PUB = datetime(2026, 11, 2, 10, 30, tzinfo=UTC)
ING = datetime(2026, 11, 3, tzinfo=UTC)
ISIN = "INE002A01018"


def _lineage(version=1, published_at=PUB):
    return {"source": "test", "source_ref": None, "published_at": published_at, "ingested_at": ING,
            "raw_sha256": "abc", "schema_version": version}


def _price(d=date(2026, 11, 2), **over):
    row = {"instrument_id": ISIN, "exchange": "NSE", "trade_date": d, "open": 100.0, "high": 110.0,
           "low": 95.0, "close": 105.0, "volume": 1000, **_lineage()}
    row.update(over)
    return row


def _t(rows):
    return pa.Table.from_pylist(rows)


def test_good_prices_pass_and_get_contract_types():
    t = conform(_t([_price()]), PRICES_DAILY)
    assert t.schema.equals(PRICES_DAILY.schema)


@pytest.mark.parametrize(
    "over,msg",
    [({"high": 90.0}, "range"), ({"close": -1.0}, "> 0"), ({"volume": -5}, "volume"), ({"schema_version": 9}, "schema_version")],
)
def test_bad_prices_are_rejected(over, msg):
    with pytest.raises(ContractError, match=msg):
        conform(_t([_price(**over)]), PRICES_DAILY)


def test_duplicate_key_is_rejected():
    with pytest.raises(ContractError, match="duplicate"):
        conform(_t([_price(), _price()]), PRICES_DAILY)


def test_missing_and_unknown_columns_are_rejected():
    row = _price()
    del row["close"]
    with pytest.raises(ContractError, match="missing"):
        conform(_t([row]), PRICES_DAILY)
    with pytest.raises(ContractError, match="unknown"):
        conform(_t([_price(adj_close=1.0)]), PRICES_DAILY)


def test_published_after_ingested_is_rejected():
    with pytest.raises(ContractError, match="published_at"):
        conform(_t([_price(published_at=datetime(2027, 1, 1, tzinfo=UTC))]), PRICES_DAILY)


def _fin(value, published_at):
    return {"instrument_id": ISIN, "period_end": date(2026, 6, 30), "period_type": "Q", "statement": "pl",
            "consolidated": True, "line_item": "revenue", "value": value, **_lineage(published_at=published_at)}


def test_first_reported_and_restated_values_both_stay():
    rows = [_fin(100.0, datetime(2026, 7, 20, tzinfo=UTC)), _fin(95.0, datetime(2026, 10, 15, tzinfo=UTC))]
    assert conform(_t(rows), FINANCIALS).num_rows == 2


def test_enum_outside_contract_is_rejected():
    row = _fin(1.0, PUB)
    row["statement"] = "notes"
    with pytest.raises(ContractError, match="statement"):
        conform(_t([row]), FINANCIALS)


class CsvPrices:
    """A stand-in vendor: returns CSV with its own column names."""

    name = "fakevendor"
    dataset = "prices_daily"
    raw_ext = "csv"

    def __init__(self, text):
        self.text = text
        self.calls = 0

    def fetch(self, **params):
        self.calls += 1
        return self.text.encode()

    def parse(self, payload):
        out = []
        for r in csv.DictReader(io.StringIO(payload.decode())):
            d = date.fromisoformat(r["Date"])
            out.append({"instrument_id": r["ISIN"], "exchange": "NSE", "trade_date": d, "open": float(r["O"]),
                        "high": float(r["H"]), "low": float(r["L"]), "close": float(r["C"]), "volume": int(r["Vol"]),
                        "published_at": datetime(d.year, d.month, d.day, 10, 0, tzinfo=UTC)})
        return out


CSV = "ISIN,Date,O,H,L,C,Vol\nINE002A01018,2025-12-31,100,110,95,105,1000\nINE002A01018,2026-01-01,105,112,104,111,1200\n"


def test_ingest_keeps_raw_and_writes_parquet_by_year(tmp_path):
    lake = Lake(tmp_path)
    paths = ingest(CsvPrices(CSV), lake)
    assert sorted(p.parent.name for p in paths) == ["year=2025", "year=2026"]
    raws = list((tmp_path / "raw").rglob("*.csv.gz"))
    assert len(raws) == 1 and Lake.load_raw(raws[0]).decode() == CSV
    rows = lake.query(f"SELECT trade_date, close, source FROM {lake.read_sql('prices_daily')} ORDER BY trade_date")
    assert rows == [(date(2025, 12, 31), 105.0, "fakevendor"), (date(2026, 1, 1), 111.0, "fakevendor")]


def test_ingesting_the_same_payload_twice_does_not_duplicate(tmp_path):
    lake = Lake(tmp_path)
    a = CsvPrices(CSV)
    ingest(a, lake)
    ingest(a, lake)
    assert len(list((tmp_path / "raw").rglob("*.csv.gz"))) == 1
    assert lake.query(f"SELECT count(*) FROM {lake.read_sql('prices_daily')}")[0][0] == 2


def test_reparse_needs_no_network(tmp_path):
    lake = Lake(tmp_path)
    a = CsvPrices(CSV)
    ingest(a, lake)
    raw = next((tmp_path / "raw").rglob("*.csv.gz"))
    reparse(a, lake, raw)
    assert a.calls == 1
    assert lake.query(f"SELECT count(*) FROM {lake.read_sql('prices_daily')}")[0][0] == 2


def test_bad_vendor_data_never_reaches_canonical(tmp_path):
    lake = Lake(tmp_path)
    bad = CSV.replace("100,110,95,105", "100,90,95,105")
    with pytest.raises(ContractError):
        ingest(CsvPrices(bad), lake)
    assert not (tmp_path / "canonical").exists()
    assert len(list((tmp_path / "raw").rglob("*.csv.gz"))) == 1  # raw is still kept for debugging


def test_every_dataset_has_lineage_and_valid_keys():
    for ds in DATASETS.values():
        names = ds.schema.names
        assert {"source", "published_at", "ingested_at", "raw_sha256", "schema_version"} <= set(names)
        assert set(ds.key) <= set(names)
        assert ds.partition_by is None or ds.partition_by in names
