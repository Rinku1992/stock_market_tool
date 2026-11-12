"""Kite commands. Run on your own machine.

    python -m smt.kite_cli login
    python -m smt.kite_cli prices RELIANCE INE002A01018 [--from 2021-01-01] [--to 2026-11-11]
    python -m smt.kite_cli holdings [--account main]
    python -m smt.kite_cli prices-holdings [--account main]
    python -m smt.kite_cli prices-index [--index IDX:NIFTY100]

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
from smt.data.holdings import latest_holdings
from smt.data.lake import Lake
from smt.data.members import index_members
from smt.sources.kite import (IST, KiteClient, KiteDailyPrices, KiteError, KiteHoldings, find_instrument_token,
                              load_token, login_url, save_token)

CALLBACK_HOST, CALLBACK_PORT, CALLBACK_PATH = "127.0.0.1", 8000, "/kite/callback"
CHUNK_DAYS = 365  # days per request. Kept small on purpose; raise it once the real limit is confirmed.
PAUSE = 0.4  # seconds between calls, to stay under the documented 3 requests per second
EXCHANGES = ("NSE", "BSE")


def _env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"{name} is not set. Add it to your .env file.")
    return v


def _lake() -> Lake:
    return Lake(os.environ.get("SMT_LAKE_PATH", "data/lake"))


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


def _instrument_dump(client: KiteClient, lake: Lake, exchange: str) -> bytes:
    dump = client.get(f"/instruments/{exchange}")
    lake.save_raw("kite", "instruments_dump", dump, "csv")
    return dump


def load_prices(client: KiteClient, lake: Lake, dump: bytes, symbol: str, instrument_id: str, exchange: str,
                start: date, end: date, verbose: bool = True, pause: float = PAUSE) -> int:
    """Pull daily prices for one instrument in yearly chunks. Returns the number of files written."""
    token = find_instrument_token(dump, symbol, exchange)
    adapter = KiteDailyPrices(client, instrument_id, exchange, token)
    files = 0
    while start <= end:
        stop = min(start + timedelta(days=CHUNK_DAYS - 1), end)
        files += len(ingest(adapter, lake, start=start, end=stop))
        if verbose:
            print(f"  {start} to {stop} ok")
        start = stop + timedelta(days=1)
        time.sleep(pause)
    return files


def cmd_login(args) -> None:
    api_key, api_secret = _env("KITE_API_KEY"), _env("KITE_API_SECRET")
    url = login_url(api_key)
    print(f"Open this URL and log in to Zerodha:\n{url}\nWaiting on http://{CALLBACK_HOST}:{CALLBACK_PORT}{CALLBACK_PATH} ...")
    webbrowser.open(url)
    client = KiteClient(api_key)
    save_token(_token_path(), client.exchange_token(_wait_for_request_token(), api_secret))
    print("Token saved. It is valid for today.")


def cmd_prices(args) -> None:
    client, lake = _client(), _lake()
    isin = args.isin.upper()
    files = load_prices(client, lake, _instrument_dump(client, lake, args.exchange), args.symbol.upper(), isin,
                        args.exchange, args.start, args.end)
    n = lake.query(f"SELECT count(*), min(trade_date), max(trade_date) FROM {lake.read_sql('prices_daily')} WHERE instrument_id = ?", [isin])[0]
    print(f"{args.symbol.upper()}: {n[0]} daily rows stored, {n[1]} to {n[2]} ({files} files written)")


def cmd_holdings(args) -> None:
    lake = _lake()
    today = datetime.now(IST).date()
    ingest(KiteHoldings(_client(), args.account, today), lake)
    n = lake.query(f"SELECT count(*) FROM {lake.read_sql('holdings')} WHERE as_of = ? AND account = ?", [today, args.account])[0][0]
    print(f"{n} holdings stored for {today}")


def load_prices_for_holdings(client: KiteClient, lake: Lake, account: str, start: date, end: date,
                             pause: float = PAUSE) -> tuple[int, list[str]]:
    """Pull prices for every stored holding. Returns (number loaded, symbols that failed)."""
    return load_prices_for(client, lake, latest_holdings(lake, account), start, end, pause)


def load_prices_for(client: KiteClient, lake: Lake, held: list[tuple[str, str | None]], start: date, end: date,
                    pause: float = PAUSE) -> tuple[int, list[str]]:
    """Pull prices for a list of (ISIN, symbol). Returns (number loaded, symbols that failed)."""
    dumps = {ex: _instrument_dump(client, lake, ex) for ex in EXCHANGES}
    done, failed = 0, []
    for k, (isin, symbol) in enumerate(held, 1):
        ok = False
        for ex in EXCHANGES:  # try NSE first, then BSE
            try:
                load_prices(client, lake, dumps[ex], symbol or "", isin, ex, start, end, verbose=False, pause=pause)
                ok = True
                break
            except KiteError:
                continue
        done += ok
        if not ok:
            failed.append(symbol or isin)
        print(f"  {k}/{len(held)} {'ok' if ok else 'FAILED'}")
    return done, failed


def cmd_prices_holdings(args) -> None:
    client, lake = _client(), _lake()
    if not latest_holdings(lake, args.account):
        sys.exit("No holdings stored. Run: python -m smt.kite_cli holdings")
    done, failed = load_prices_for_holdings(client, lake, args.account, args.start, args.end)
    print(f"Prices loaded for {done} holdings. Failed: {len(failed)}" + (f" ({', '.join(failed)})" if failed else ""))


def cmd_prices_index(args) -> None:
    client, lake = _client(), _lake()
    members = index_members(lake, args.index)
    if not members:
        sys.exit(f"No member list stored for {args.index}. Run: python -m smt.data_cli index-list <file>")
    done, failed = load_prices_for(client, lake, members, args.start, args.end)
    print(f"Prices loaded for {done} of {len(members)} members. Failed: {len(failed)}" + (f" ({', '.join(failed)})" if failed else ""))


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    yesterday = datetime.now(IST).date() - timedelta(days=1)  # today's candle is not final until close
    p = argparse.ArgumentParser(prog="smt.kite_cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login").set_defaults(fn=cmd_login)

    def dates(sp):
        sp.add_argument("--from", dest="start", type=date.fromisoformat, default=yesterday - timedelta(days=5 * 365))
        sp.add_argument("--to", dest="end", type=date.fromisoformat, default=yesterday)

    pr = sub.add_parser("prices")
    pr.add_argument("symbol")
    pr.add_argument("isin")
    pr.add_argument("--exchange", default="NSE")
    dates(pr)
    pr.set_defaults(fn=cmd_prices)
    ho = sub.add_parser("holdings")
    ho.add_argument("--account", default="main")
    ho.set_defaults(fn=cmd_holdings)
    ph = sub.add_parser("prices-holdings")
    ph.add_argument("--account", default="main")
    dates(ph)
    ph.set_defaults(fn=cmd_prices_holdings)
    pi = sub.add_parser("prices-index")
    pi.add_argument("--index", default="IDX:NIFTY100")
    dates(pi)
    pi.set_defaults(fn=cmd_prices_index)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
