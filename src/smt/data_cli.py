"""Checks on stored data, and loading files saved by hand.

    python -m smt.data_cli gaps INE002A01018 [--pct 20]
    python -m smt.data_cli index-list data/ind_nifty100list.csv [--index IDX:NIFTY100]

`gaps` lists days where the close moved more than --pct percent from the previous close.
A large one-day drop on a split or bonus date means the prices are NOT adjusted.
"""

import argparse
import os
from datetime import date

from dotenv import load_dotenv

from smt.data.adapter import ingest
from smt.data.lake import Lake
from smt.data.members import index_members
from smt.sources.nse_files import NseIndexListFile


def find_gaps(lake: Lake, instrument_id: str, pct: float = 20.0) -> list[tuple]:
    sql = f"""
        SELECT trade_date, prev_close, close, round((close / prev_close - 1) * 100, 1) AS change_pct
        FROM (SELECT trade_date, close, lag(close) OVER (ORDER BY trade_date) AS prev_close
              FROM {lake.read_sql('prices_daily')} WHERE instrument_id = ?)
        WHERE prev_close IS NOT NULL AND abs(close / prev_close - 1) * 100 > ?
        ORDER BY trade_date"""
    return lake.query(sql, [instrument_id, pct])


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
    args = p.parse_args(argv)
    lake = Lake(os.environ.get("SMT_LAKE_PATH", "data/lake"))
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
