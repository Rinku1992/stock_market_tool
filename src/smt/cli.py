"""Run the engine for one stock.

    python -m smt.cli RELIANCE --isin INE002A01018 [--as-of 2026-11-11]

Load prices first (see `python -m smt.kite_cli`). For strength versus the market, also load the index:
    python -m smt.kite_cli prices "NIFTY 50" IDX:NIFTY50
"""

import argparse
import os
from datetime import date

from dotenv import load_dotenv

from smt.data.lake import Lake
from smt.graph import run
from smt.store import FeatureStore


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    p = argparse.ArgumentParser(prog="smt.cli")
    p.add_argument("symbol")
    p.add_argument("--isin", help="ISIN of the stock. Needed until the symbol-to-ISIN list is loaded.")
    p.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    args = p.parse_args(argv)

    store = FeatureStore(os.environ.get("SMT_DB_PATH", "data/smt.duckdb"))
    lake = Lake(os.environ.get("SMT_LAKE_PATH", "data/lake"))
    state = run(args.symbol, args.as_of, store, lake=lake, instrument_id=args.isin.upper() if args.isin else None)

    v = state["verdict"]
    print(f"\n{v.symbol} as of {v.as_of}  |  data grade {v.data_grade.value}")
    if v.refused_reason:
        print(f"REFUSED: {v.refused_reason}")
        return
    print("\nAnalyst reports")
    for r in sorted(state.get("reports", []), key=lambda r: (r.unit, r.analyst_id)):
        if r.finding == "not built yet":
            continue
        views = ", ".join(f"{x.horizon.value}: {x.direction.value}" for x in r.views)
        print(f"- [{r.unit}] {r.analyst_id}: {r.finding}")
        print(f"    views -> {views}")
        for n in r.could_not_check:
            print(f"    note  -> {n}")
    print("\nVerdict")
    for h in v.horizons:
        print(f"- {h.horizon.value}: {h.call.value} ({'; '.join(h.reasons)})")
    built = sum(1 for r in state.get("reports", []) if r.finding != "not built yet")
    print(f"\n{built} real analysts ran, {len(state.get('reports', [])) - built} placeholders. Run id {state['run_id']}")


if __name__ == "__main__":
    main()
