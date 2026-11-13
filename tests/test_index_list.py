from datetime import date

import pytest

from smt.data.adapter import ingest
from smt.data.lake import Lake
from smt.data.members import index_members
from smt.sources.nse_files import IndexListError, NseIndexListFile, parse_index_list

# Example layout for tests only. The real NSE file's headers are not verified here;
# the parser matches columns by name so it does not depend on the exact layout.
GOOD = ("﻿Company Name,Industry,Symbol,Series,ISIN Code\n"
        "Reliance Industries Ltd.,Oil Gas,RELIANCE,EQ,INE002A01018\n"
        "Infosys Ltd.,IT,INFY,EQ,INE009A01021\n"
        "Some Partly Paid,Misc,PPX,E1,INE999A01011\n"
        "Infosys Ltd.,IT,INFY,EQ,INE009A01021\n").encode()


def test_parse_matches_columns_by_name_and_skips_non_eq_and_duplicates():
    assert parse_index_list(GOOD) == [("RELIANCE", "INE002A01018", "Oil Gas"), ("INFY", "INE009A01021", "IT")]
    assert parse_index_list(b"Symbol,ISIN Code\nRELIANCE,INE002A01018\n") == [("RELIANCE", "INE002A01018", None)]


def test_rebuild_index_members_from_stored_raw(tmp_path):
    import shutil

    from smt.data_cli import rebuild_index_members

    f = tmp_path / "list.csv"
    f.write_bytes(GOOD)
    lake = Lake(tmp_path / "lake")
    ingest(NseIndexListFile(f, "IDX:NIFTY100", date(2026, 11, 1)), lake)
    f.unlink()  # the owner's file is gone: the rebuild must work from the stored raw copy
    shutil.rmtree(lake.canonical / "index_members")
    assert index_members(lake, "IDX:NIFTY100") == []
    assert rebuild_index_members(lake) == 1
    assert index_members(lake, "IDX:NIFTY100") == [("INE009A01021", "INFY"), ("INE002A01018", "RELIANCE")]
    got = lake.query(f"SELECT instrument_id, industry FROM {lake.read_sql('index_members')} ORDER BY 1")
    assert got == [("INE002A01018", "Oil Gas"), ("INE009A01021", "IT")]


def test_missing_isin_column_fails_loudly():
    with pytest.raises(IndexListError, match="ISIN"):
        parse_index_list(b"Symbol,Name\nRELIANCE,Reliance\n")


def test_bad_isin_fails_loudly():
    with pytest.raises(IndexListError, match="bad ISIN"):
        parse_index_list(b"Symbol,ISIN Code\nRELIANCE,12345\n")


def test_ingest_and_read_latest_list(tmp_path):
    f = tmp_path / "list.csv"
    f.write_bytes(GOOD)
    lake = Lake(tmp_path / "lake")
    ingest(NseIndexListFile(f, "IDX:NIFTY100", date(2026, 11, 1)), lake)
    f.write_bytes(b"Symbol,ISIN Code\nRELIANCE,INE002A01018\n")
    ingest(NseIndexListFile(f, "IDX:NIFTY100", date(2026, 11, 12)), lake)
    assert index_members(lake, "IDX:NIFTY100") == [("INE002A01018", "RELIANCE")]
    assert index_members(lake, "IDX:OTHER") == []


def test_top_holdings_by_money_invested(tmp_path):
    import json
    import httpx
    from smt.data.holdings import top_holdings
    from smt.sources.kite import KiteClient, KiteHoldings
    held = json.dumps({"status": "success", "data": [
        {"tradingsymbol": "SMALL", "isin": "INE000000001", "quantity": 100, "t1_quantity": 0, "average_price": 10.0},
        {"tradingsymbol": "BIG", "isin": "INE000000002", "quantity": 10, "t1_quantity": 0, "average_price": 500.0},
        {"tradingsymbol": "MID", "isin": "INE000000003", "quantity": 20, "t1_quantity": 0, "average_price": 100.0},
    ]}).encode()
    client = KiteClient("k", "t", http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=held))))
    lake = Lake(tmp_path)
    ingest(KiteHoldings(client, "main", date(2026, 11, 13)), lake)
    top = top_holdings(lake, 2)
    assert [s for _, s, _ in top] == ["BIG", "MID"]  # 5,000 and 2,000 invested; SMALL has 1,000
    assert round(top[0][2], 3) == 0.625


def test_symbol_to_isin_uses_the_index_list(tmp_path):
    from smt.data.members import symbol_to_isin
    f = tmp_path / "list.csv"
    f.write_bytes(GOOD)
    lake = Lake(tmp_path / "lake")
    ingest(NseIndexListFile(f, "IDX:NIFTY100", date(2026, 11, 1)), lake)
    assert symbol_to_isin(lake)["INFY"] == "INE009A01021"
