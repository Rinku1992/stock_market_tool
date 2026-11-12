"""Kite adapter tests. No network: payloads follow the shapes in the public API docs."""

import hashlib
import json
from datetime import date, datetime, timezone

import httpx
import pytest

from smt.data.adapter import ingest
from smt.data.lake import Lake
from smt.sources.kite import (KiteClient, KiteDailyPrices, KiteError, KiteHoldings, checksum, find_instrument_token,
                              load_token, login_url, save_token)

ISIN = "INE002A01018"
CANDLES = json.dumps({"status": "success", "data": {"candles": [
    ["2026-11-09T00:00:00+0530", 1500.0, 1520.5, 1490.0, 1510.25, 4500000],
    ["2026-11-10T00:00:00+0530", 1510.0, 1530.0, 1505.0, 1525.0, 3900000],
]}}).encode()
HOLDINGS = json.dumps({"status": "success", "data": [
    {"tradingsymbol": "RELIANCE", "exchange": "NSE", "isin": ISIN, "quantity": 10, "t1_quantity": 2, "average_price": 1400.5},
    {"tradingsymbol": "SOLD", "exchange": "NSE", "isin": "INE000000000", "quantity": 0, "t1_quantity": 0, "average_price": 10.0},
]}).encode()
DUMP = (b"instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,tick_size,lot_size,instrument_type,segment,exchange\n"
        b"738561,2885,RELIANCE,RELIANCE INDUSTRIES,0,,0,0.1,1,EQ,NSE,NSE\n"
        b"128083204,500325,RELIANCE,RELIANCE INDUSTRIES,0,,0,0.05,1,EQ,BSE,BSE\n")


def test_login_url_and_checksum():
    assert login_url("k") == "https://kite.zerodha.com/connect/login?v=3&api_key=k"
    assert checksum("k", "r", "s") == hashlib.sha256(b"krs").hexdigest()


def test_token_cache_expires_next_day(tmp_path):
    p = tmp_path / "t.json"
    save_token(p, "abc", today=date(2026, 11, 12))
    assert load_token(p, today=date(2026, 11, 12)) == "abc"
    assert load_token(p, today=date(2026, 11, 13)) is None


def test_find_instrument_token_picks_the_right_exchange():
    assert find_instrument_token(DUMP, "RELIANCE", "NSE") == 738561
    with pytest.raises(KiteError):
        find_instrument_token(DUMP, "NOPE", "NSE")


def _client(handler):
    return KiteClient("key", "tok", http=httpx.Client(transport=httpx.MockTransport(handler)))


def test_client_sends_auth_header_and_exchanges_token():
    seen = {}

    def handler(req: httpx.Request):
        seen[req.url.path] = req
        if req.url.path == "/session/token":
            return httpx.Response(200, json={"status": "success", "data": {"access_token": "new"}})
        return httpx.Response(200, content=CANDLES)

    c = _client(handler)
    c.get("/instruments/historical/738561/day", {"from": "a", "to": "b"})
    req = seen["/instruments/historical/738561/day"]
    assert req.headers["authorization"] == "token key:tok" and req.headers["x-kite-version"] == "3"
    assert c.exchange_token("rt", "sec") == "new"
    assert f"checksum={checksum('key', 'rt', 'sec')}" in seen["/session/token"].content.decode()


def test_http_error_is_raised():
    with pytest.raises(KiteError, match="403"):
        _client(lambda req: httpx.Response(403, text="token expired")).get("/x")


def test_prices_parse_to_contract_rows():
    rows = KiteDailyPrices(None, ISIN, "NSE", 738561).parse(CANDLES)
    assert rows[0]["trade_date"] == date(2026, 11, 9) and rows[0]["close"] == 1510.25
    assert rows[0]["price_basis"] == "adjusted"
    assert rows[0]["published_at"] == datetime(2026, 11, 9, 10, 0, tzinfo=timezone.utc)


def test_error_payload_is_raised():
    with pytest.raises(KiteError):
        KiteDailyPrices(None, ISIN, "NSE", 1).parse(b'{"status": "error", "message": "bad"}')


def test_prices_ingest_end_to_end(tmp_path):
    lake = Lake(tmp_path)
    adapter = KiteDailyPrices(_client(lambda req: httpx.Response(200, content=CANDLES)), ISIN, "NSE", 738561)
    ingest(adapter, lake, start=date(2026, 11, 9), end=date(2026, 11, 10))
    rows = lake.query(f"SELECT trade_date, close, source FROM {lake.read_sql('prices_daily')} ORDER BY 1")
    assert rows == [(date(2026, 11, 9), 1510.25, "kite"), (date(2026, 11, 10), 1525.0, "kite")]
    manifest = json.loads((tmp_path / "raw" / "manifest.jsonl").read_text().splitlines()[0])
    assert manifest["meta"]["instrument_id"] == ISIN and manifest["meta"]["start"] == "2026-11-09"


def test_holdings_ingest_skips_zero_quantity(tmp_path):
    lake = Lake(tmp_path)
    adapter = KiteHoldings(_client(lambda req: httpx.Response(200, content=HOLDINGS)), "main", date(2026, 11, 11))
    ingest(adapter, lake)
    rows = lake.query(f"SELECT instrument_id, quantity, avg_price FROM {lake.read_sql('holdings')}")
    assert rows == [(ISIN, 12.0, 1400.5)]


def test_prices_for_all_holdings_tries_bse_and_reports_failures(tmp_path):
    from smt.kite_cli import load_prices_for_holdings

    nse = DUMP.split(b"\n")[0] + b"\n738561,2885,RELIANCE,RELIANCE INDUSTRIES,0,,0,0.1,1,EQ,NSE,NSE\n"
    bse = DUMP.split(b"\n")[0] + b"\n999,1,ONLYBSE,ONLY ON BSE,0,,0,0.05,1,EQ,BSE,BSE\n"
    held = json.dumps({"status": "success", "data": [
        {"tradingsymbol": "RELIANCE", "isin": ISIN, "quantity": 1, "t1_quantity": 0, "average_price": 1.0},
        {"tradingsymbol": "ONLYBSE", "isin": "INE111111111", "quantity": 1, "t1_quantity": 0, "average_price": 1.0},
        {"tradingsymbol": "GONE", "isin": "INE222222222", "quantity": 1, "t1_quantity": 0, "average_price": 1.0},
    ]}).encode()

    def handler(req):
        if req.url.path == "/instruments/NSE":
            return httpx.Response(200, content=nse)
        if req.url.path == "/instruments/BSE":
            return httpx.Response(200, content=bse)
        return httpx.Response(200, content=CANDLES)

    lake, client = Lake(tmp_path), _client(handler)
    ingest(KiteHoldings(_client(lambda r: httpx.Response(200, content=held)), "main", date(2026, 11, 11)), lake)
    done, failed = load_prices_for_holdings(client, lake, "main", date(2026, 11, 9), date(2026, 11, 10), pause=0)
    assert (done, failed) == (2, ["GONE"])
    got = lake.query(f"SELECT DISTINCT instrument_id, exchange FROM {lake.read_sql('prices_daily')} ORDER BY 1")
    assert got == [(ISIN, "NSE"), ("INE111111111", "BSE")]
