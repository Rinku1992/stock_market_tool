"""Kite commands. Run on your own machine.

    python -m smt.kite_cli login
    python -m smt.kite_cli prices RELIANCE INE002A01018 [--from 2021-01-01] [--to 2026-11-11]
    python -m smt.kite_cli holdings [--account main]

Reads KITE_API_KEY and KITE_API_SECRET from .env. Run `login` once each day.
"""

import argparse
import os
import sys
import time
import webbrowser
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv

from smt.data.adapter import ingest
from smt.data.lake import Lake
from smt.sources.kite import IST, KiteClient, KiteDailyPrices, KiteHoldings, find_instrument_token, load_token, login_url, save_token

CALLBACK_HOST, CALLBACK_PORT, CALLBACK_PATH = "127.0.0.1", 8000, "/kite/callback"
CHUNK_DAYS = 365  # days per request. Kept small on purpose; raise it once the real limit is confirmed.
PAUSE = 0.4  # seconds between calls, to stay under the documented 3 requests per second


def _env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"{name} is not set. Add it to your .env file.")
    return v


def _token_path() -> str:
    return os.environ.get("SMT_KITE_TOKEN_PATH", "data/.kite_token.json")


def _client() -> KiteClient:
    token = load_token(_token_path())
    if not token:
        sys.exit("No Kite token for today. Run: python -m smt.kite_cli login")
    return KiteClient(_env("KITE_API_KEY"), token)


def _wait_for_request_token() -> str:
    got: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            token = parse_qs(url.query).get("request_token", [None])[0]
            ok = url.path == CALLBACK_PATH and token
            if ok:
                got["token"] = token
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Login done. You can close this tab." if ok else b"No request_token found.")

        def log_message(self, *args):  # keep the token out of the console
            pass

    server = HTTPServer((CALLBACK_HOST, CALLBACK_PORT), Handler)
    while "token" not in got:
        server.handle_request()
    server.server_close()
    return got["token"]


def cmd_login(args) -> None:
    api_key, api_secret = _env("KITE_API_KEY"), _env("KITE_API_SECRET")
    url = login_url(api_key)
    print(f"Open this URL and log in to Zerodha:\n{url}\nWaiting on http://{CALLBACK_HOST}:{CALLBACK_PORT}{CALLBACK_PATH} ...")
    webbrowser.open(url)
    client = KiteClient(api_key)
    save_token(_token_path(), client.exchange_token(_wait_for_request_token(), api_secret))
    print("Token saved. It is valid for today.")


def cmd_prices(args) -> None:
    client, lake = _client(), Lake(os.environ.get("SMT_LAKE_PATH", "data/lake"))
    dump = client.get(f"/instruments/{args.exchange}")
    lake.save_raw("kite", "instruments_dump", dump, "csv")
    token = find_instrument_token(dump, args.symbol.upper(), args.exchange)
    adapter = KiteDailyPrices(client, args.isin.upper(), args.exchange, token)
    start, end, files = args.start, args.end, 0
    while start <= end:
        stop = min(start + timedelta(days=CHUNK_DAYS - 1), end)
        files += len(ingest(adapter, lake, start=start, end=stop))
        print(f"  {start} to {stop} ok")
        start = stop + timedelta(days=1)
        time.sleep(PAUSE)
    n = lake.query(f"SELECT count(*), min(trade_date), max(trade_date) FROM {lake.read_sql('prices_daily')} WHERE instrument_id = ?", [args.isin.upper()])[0]
    print(f"{args.symbol.upper()}: {n[0]} daily rows stored, {n[1]} to {n[2]} ({files} files written)")


def cmd_holdings(args) -> None:
    lake = Lake(os.environ.get("SMT_LAKE_PATH", "data/lake"))
    today = datetime.now(IST).date()
    ingest(KiteHoldings(_client(), args.account, today), lake)
    n = lake.query(f"SELECT count(*) FROM {lake.read_sql('holdings')} WHERE as_of = ? AND account = ?", [today, args.account])[0][0]
    print(f"{n} holdings stored for {today}")


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    yesterday = datetime.now(IST).date() - timedelta(days=1)  # today's candle is not final until close
    p = argparse.ArgumentParser(prog="smt.kite_cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login").set_defaults(fn=cmd_login)
    pr = sub.add_parser("prices")
    pr.add_argument("symbol")
    pr.add_argument("isin")
    pr.add_argument("--exchange", default="NSE")
    pr.add_argument("--from", dest="start", type=date.fromisoformat, default=yesterday - timedelta(days=5 * 365))
    pr.add_argument("--to", dest="end", type=date.fromisoformat, default=yesterday)
    pr.set_defaults(fn=cmd_prices)
    ho = sub.add_parser("holdings")
    ho.add_argument("--account", default="main")
    ho.set_defaults(fn=cmd_holdings)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
