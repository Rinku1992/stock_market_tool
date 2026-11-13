"""Record today's calls, after the market close. Never for a past date.

    python -m smt.kite_cli update-prices                     # first: bring prices up to today
    python -m smt.record --holdings --index IDX:NIFTY100     # run the engine and record the calls
    python -m smt.record status                              # how many calls exist per day
"""

import argparse
import os
import sys
from datetime import date

from dotenv import load_dotenv

from smt.calls import IST, RecordRefused, record_day, status
from smt.data.holdings import latest_holdings
from smt.data.lake import Lake
from smt.data.members import index_members
from smt.store import FeatureStore


def print_status(lake: Lake) -> None:
    rows = status(lake)
    if not rows:
        print("No calls recorded yet.")
        return
    print(f"{'day':10} {'stocks':>6} {'calls':>6}  {'recorded (India time)':23} code version")
    for d, n, c, first, last, ver in rows:
        span = f"{first.astimezone(IST):%H:%M}" + ("" if first == last else f" to {last.astimezone(IST):%H:%M}")
        print(f"{d!s:10} {n:>6} {c:>6}  {span:23} {ver}")
    print(f"\n{len(rows)} day(s), {sum(r[2] for r in rows)} calls in total.")


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    argv = sys.argv[1:] if argv is None else argv
    lake = Lake(os.environ.get("SMT_LAKE_PATH", "data/lake"))
    if argv[:1] == ["status"]:
        print_status(lake)
        return
    p = argparse.ArgumentParser(prog="smt.record")
    p.add_argument("--holdings", action="store_true", help="every stock in your stored holdings")
    p.add_argument("--account", default="main")
    p.add_argument("--index", help="every stored member of this index, e.g. IDX:NIFTY100")
    p.add_argument("--as-of", type=date.fromisoformat, help="only today is accepted; any other date is refused")
    args = p.parse_args(argv)
    stocks = []
    if args.holdings:
        stocks += latest_holdings(lake, args.account)
    if args.index:
        stocks += index_members(lake, args.index)
    if not stocks:
        p.error("no stocks: use --holdings and/or --index (and check they are loaded)")
    store = FeatureStore(os.environ.get("SMT_DB_PATH", "data/smt.duckdb"))
    try:
        res = record_day(lake, store, stocks, as_of=args.as_of)
    except RecordRefused as e:
        sys.exit(f"NOT RECORDED: {e}")
    print(f"{res.as_of}: recorded {len(res.recorded)} stock(s), {res.rows} calls. "
          f"Already recorded today: {len(res.already)}. Refused by the data office: {len(res.refused)}.")
    for sym, why in res.refused:
        print(f"  refused {sym}: {why}")
    print()
    print_status(lake)


if __name__ == "__main__":
    main()
