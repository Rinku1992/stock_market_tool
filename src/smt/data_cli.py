"""Checks on stored data, and loading files saved by hand.

    python -m smt.data_cli gaps INE002A01018 [--pct 20]
    python -m smt.data_cli index-list data/ind_nifty100list.csv [--index IDX:NIFTY100]
    python -m smt.data_cli results data/<file>.xml [more files ...] [--broadcast "2025-01-16 20:15:20"]

`gaps` lists days where the close moved more than --pct percent from the previous close.
A large one-day drop on a split or bonus date means the prices are NOT adjusted.
"""

import argparse
import os
from datetime import date, datetime

from dotenv import load_dotenv

from smt.data.adapter import ingest
from smt.data.lake import Lake
from smt.data.members import index_members
from smt.data.holdings import latest_holdings
from smt.sources.nse_files import NseIndexListFile
from smt.sources.nse_results import IST, NseResultsXbrlFile, parse_results_xbrl


def find_gaps(lake: Lake, instrument_id: str, pct: float = 20.0) -> list[tuple]:
    sql = f"""
        SELECT trade_date, prev_close, close, round((close / prev_close - 1) * 100, 1) AS change_pct
        FROM (SELECT trade_date, close, lag(close) OVER (ORDER BY trade_date) AS prev_close
              FROM {lake.read_sql('prices_daily')} WHERE instrument_id = ?)
        WHERE prev_close IS NOT NULL AND abs(close / prev_close - 1) * 100 > ?
        ORDER BY trade_date"""
    return lake.query(sql, [instrument_id, pct])


def symbol_to_isin(lake: Lake) -> dict[str, str]:
    known = {}
    for isin, sym in latest_holdings(lake) + index_members(lake, "IDX:NIFTY100"):
        if sym:
            known[sym.upper()] = isin
    return known


def load_results(lake: Lake, paths: list[str], isin: str | None = None, broadcast: str | None = None) -> None:
    if broadcast and len(paths) > 1:
        raise SystemExit("--broadcast works with one file at a time")
    when = datetime.fromisoformat(broadcast).replace(tzinfo=IST) if broadcast else None
    known = None
    for path in paths:
        with open(path, "rb") as f:
            rf = parse_results_xbrl(f.read())
        use = isin or rf.isin
        if not use:
            known = known if known is not None else symbol_to_isin(lake)
            use = known.get(rf.symbol)
        if not use:
            raise SystemExit(f"{path}: no ISIN in the file and {rf.symbol} is not in your holdings or the Nifty 100. Use --isin.")
        ingest(NseResultsXbrlFile(path, isin=use, broadcast_ist=when), lake)
        kind = "consolidated" if rf.consolidated else "standalone"
        print(f"{rf.symbol} {kind}, period ended {rf.period_end}: {len(rf.facts)} figures stored")


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    p = argparse.ArgumentParser(prog="smt.data_cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gaps")
    g.add_argument("isin")
    g.add_argument("--pct", type=float, default=20.0)
    il = sub.add_parser("index-list", help="load an index member list CSV you downloaded from NSE")
    il.add_argument("path")
    il.add_argument("--index", default="IDX:NIFTY100")
    il.add_argument("--as-of", type=date.fromisoformat, default=date.today(), help="day you downloaded the file")
    rs = sub.add_parser("results", help="load quarterly results XBRL files you downloaded from NSE")
    rs.add_argument("paths", nargs="+")
    rs.add_argument("--isin", help="needed only for older files that have no ISIN and a symbol we cannot match")
    rs.add_argument("--broadcast", help='NSE broadcast time, IST, e.g. "2025-01-16 20:15:20" (only with one file)')
    args = p.parse_args(argv)
    lake = Lake(os.environ.get("SMT_LAKE_PATH", "data/lake"))
    if args.cmd == "results":
        load_results(lake, args.paths, args.isin, args.broadcast)
        return
    if args.cmd == "index-list":
        ingest(NseIndexListFile(args.path, args.index, args.as_of), lake)
        print(f"{len(index_members(lake, args.index))} members stored for {args.index} (as of {args.as_of})")
        return
    rows = find_gaps(lake, args.isin.upper(), args.pct)
    if not rows:
        print(f"No one-day move above {args.pct}% found.")
    for d, prev, close, chg in rows:
        print(f"{d}  prev close {prev}  close {close}  change {chg}%")


if __name__ == "__main__":
    main()
