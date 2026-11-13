"""Checks on stored data, and loading files saved by hand.

    python -m smt.data_cli gaps INE002A01018 [--pct 20]
    python -m smt.data_cli index-list data/ind_nifty100list.csv [--index IDX:NIFTY100]
    python -m smt.data_cli results data/<file>.xml [more files ...] [--broadcast "2025-01-16 20:15:20"]
    python -m smt.data_cli top-holdings [--n 10]

`gaps` lists days where the close moved more than --pct percent from the previous close.
A large one-day drop on a split or bonus date means the prices are NOT adjusted.
"""

import argparse
import os
from datetime import date, datetime

from dotenv import load_dotenv

from smt.data.adapter import ingest, reparse
from smt.data.lake import Lake
from smt.data.members import index_members, symbol_to_isin
from smt.data.holdings import latest_holdings, loaded_quarters, top_holdings
from smt.sources.nse_files import NseIndexListFile
from smt.sources.nse_results import IST, NseResultsSegmentsFile, NseResultsXbrlFile, parse_results_xbrl


def find_gaps(lake: Lake, instrument_id: str, pct: float = 20.0) -> list[tuple]:
    sql = f"""
        SELECT trade_date, prev_close, close, round((close / prev_close - 1) * 100, 1) AS change_pct
        FROM (SELECT trade_date, close, lag(close) OVER (ORDER BY trade_date) AS prev_close
              FROM {lake.read_sql('prices_daily')} WHERE instrument_id = ?)
        WHERE prev_close IS NOT NULL AND abs(close / prev_close - 1) * 100 > ?
        ORDER BY trade_date"""
    return lake.query(sql, [instrument_id, pct])


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
        if rf.segments:
            ingest(NseResultsSegmentsFile(path, isin=use, broadcast_ist=when), lake)
        kind = "consolidated" if rf.consolidated else "standalone"
        print(f"{rf.symbol} {kind}, period ended {rf.period_end}: {len(rf.facts)} figures stored")


def rebuild_index_members(lake: Lake) -> int:
    """Re-parse every stored raw index list into the current index_members version. No file needed."""
    import json
    from pathlib import Path

    manifest, n = lake.raw / "manifest.jsonl", 0
    for line in manifest.read_text().splitlines() if manifest.exists() else []:
        m = json.loads(line)
        if m["source"] != "nse_manual_file" or m["dataset"] != "index_members":
            continue
        meta = m["meta"]
        adapter = NseIndexListFile(meta["file"], meta["index_id"], date.fromisoformat(meta["as_of"]))
        reparse(adapter, lake, lake.root / m["path"])
        n += 1
    return n


def print_top_holdings(lake: Lake, n: int) -> None:
    top = top_holdings(lake, n)
    if not top:
        print("No holdings stored. Run: python -m smt.kite_cli holdings")
        return
    print(f"{'#':>2}  {'symbol':12} {'ISIN':13} {'share':>6}  consolidated quarters loaded")
    for i, (isin, sym, share) in enumerate(top, 1):
        qs = ", ".join(d.strftime("%b %Y") for d in loaded_quarters(lake, isin)) or "none"
        print(f"{i:>2}  {(sym or '?'):12} {isin:13} {share:>6.1%}  {qs}")
    print(f"\nThese {len(top)} hold {sum(s for _, _, s in top):.0%} of the money you invested (by buy price).")


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
    rb = sub.add_parser("rebuild", help="re-parse stored raw files into the current schema version")
    rb.add_argument("dataset", choices=["index_members"])
    th = sub.add_parser("top-holdings", help="your largest holdings by money invested, and which results are loaded")
    th.add_argument("--n", type=int, default=10)
    args = p.parse_args(argv)
    lake = Lake(os.environ.get("SMT_LAKE_PATH", "data/lake"))
    if args.cmd == "rebuild":
        n = rebuild_index_members(lake)
        print(f"Re-parsed {n} stored index list file(s). Nifty 100 members now: {len(index_members(lake, 'IDX:NIFTY100'))}")
        return
    if args.cmd == "top-holdings":
        print_top_holdings(lake, args.n)
        return
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
