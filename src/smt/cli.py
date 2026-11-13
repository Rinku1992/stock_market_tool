"""Run the engine for one stock.

    python -m smt.cli TITAN [--as-of 2026-11-11]
    python -m smt.cli SOMESTOCK --isin INE000X01010   # only if the symbol is not in your holdings or the Nifty 100

Load prices first (see `python -m smt.kite_cli`). For strength versus the market, also load the index:
    python -m smt.kite_cli prices "NIFTY 50" IDX:NIFTY50
"""

import argparse
import os
from datetime import date

from dotenv import load_dotenv

from smt.data.lake import Lake
from smt.data.members import symbol_to_isin
from smt.graph import run
from smt.store import FeatureStore


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    p = argparse.ArgumentParser(prog="smt.cli")
    p.add_argument("symbol")
    p.add_argument("--isin", help="ISIN, only if the symbol is not in your holdings or the Nifty 100 list")
    p.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    args = p.parse_args(argv)

    store = FeatureStore(os.environ.get("SMT_DB_PATH", "data/smt.duckdb"))
    lake = Lake(os.environ.get("SMT_LAKE_PATH", "data/lake"))
    isin = args.isin.upper() if args.isin else symbol_to_isin(lake).get(args.symbol.upper())
    if not isin:
        p.error(f"{args.symbol.upper()} is not in your holdings or the Nifty 100 list. Add --isin.")
    state = run(args.symbol, args.as_of, store, lake=lake, instrument_id=isin)

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
    for d in state.get("desk_reports", []):
        if not d.facts:
            continue
        print(f"\nDesk lead: {d.unit}  (weights version {d.weights_version}, summary by {d.summary_source})")
        print("  views -> " + ", ".join(f"{x.horizon.value}: {x.direction.value}" for x in d.views))
        for c in d.conflicts:
            print(f"  conflict ({c.horizon.value}) -> positive: {', '.join(c.positive)}; negative: {', '.join(c.negative)}")
        for line in d.summary.splitlines():
            print(f"  {line}")
        if d.summary_rejected:
            print(f"  (LLM summary rejected: {d.summary_rejected[0]})")
    print("\nVerdict")
    for h in v.horizons:
        print(f"- {h.horizon.value}: {h.call.value} ({'; '.join(h.reasons)})")
    built = sum(1 for r in state.get("reports", []) if r.finding != "not built yet")
    print(f"\n{built} real analysts ran, {len(state.get('reports', [])) - built} placeholders. Run id {state['run_id']}")


if __name__ == "__main__":
    main()
