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
    assert parse_index_list(GOOD) == [("RELIANCE", "INE002A01018"), ("INFY", "INE009A01021")]


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
