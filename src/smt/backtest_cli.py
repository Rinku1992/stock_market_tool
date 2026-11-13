"""Backtest an analyst's views on stored prices.

    python -m smt.backtest_cli price_structure INE002A01018 [more ISINs ...]
    python -m smt.backtest_cli price_structure --holdings
    python -m smt.backtest_cli momentum --index IDX:NIFTY100

Load prices for every stock and for the index first (python -m smt.kite_cli prices ...).
"""

import argparse
import os

from dotenv import load_dotenv

from smt.analysts.market_activity import momentum, price_structure
from smt.backtest import BENCHMARKS, FWD_DAYS, MIN_TRUSTED_N, MIN_UNIVERSE_BENCH, run_backtest, summarize
from smt.data.holdings import latest_holdings
from smt.data.lake import Lake
from smt.data.members import index_members

ANALYSTS = {"momentum": momentum, "price_structure": price_structure}


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    p = argparse.ArgumentParser(prog="smt.backtest_cli")
    p.add_argument("analyst", choices=sorted(ANALYSTS))
    p.add_argument("isins", nargs="*")
    p.add_argument("--holdings", action="store_true", help="use every stock in your stored holdings")
    p.add_argument("--account", default="main")
    p.add_argument("--index", help="use every stored member of this index, e.g. IDX:NIFTY100")
    p.add_argument("--vs", choices=BENCHMARKS, help="benchmark: universe median (default with 10+ stocks) or nifty")
    args = p.parse_args(argv)
    lake = Lake(os.environ.get("SMT_LAKE_PATH", "data/lake"))
    ids = [i.upper() for i in args.isins]
    if args.holdings:
        ids += [i for i, _ in latest_holdings(lake, args.account) if i not in ids]
    if args.index:
        ids += [i for i, _ in index_members(lake, args.index) if i not in ids]
    if not ids:
        p.error("give at least one ISIN, or use --holdings or --index")
    vs = args.vs or ("universe" if len(ids) >= MIN_UNIVERSE_BENCH else "nifty")
    obs = run_backtest(lake, ids, ANALYSTS[args.analyst], vs=vs)
    if not obs:
        print("No views could be scored. Check that prices are loaded for the stocks and the index.")
        return
    dates = sorted({o.as_of for o in obs})
    print(f"\nBacktest: {args.analyst} | {len(ids)} stock(s) | monthly, {dates[0]} to {dates[-1]}")
    bench = f"the median of all {len(ids)} stocks on the same date" if vs == "universe" else "the Nifty 50"
    print(f"Scored on return versus {bench}, after "
          + ", ".join(f"{FWD_DAYS[h]} trading days ({h.value})" for h in FWD_DAYS) + "\n")
    print(f"{'horizon':8} {'view':9} {'n':>5} {'stocks':>6} {'mean excess':>12} {'median':>8} {'hit rate':>9}")
    small = False
    for r in summarize(obs):
        hit = "-" if r.hit_rate is None else f"{r.hit_rate:.0%}"
        flag = ""
        if r.view != "ALL" and r.n < MIN_TRUSTED_N:
            flag, small = " *", True
        print(f"{r.horizon.value:8} {r.view:9} {r.n:>5} {r.n_stocks:>6} {r.mean_excess:>+12.1%} {r.median_excess:>+8.1%} {hit:>9}{flag}")
    print("\nHow to read this")
    print(f"- mean excess: average return of the stock minus {bench}, after the view was given.")
    print("- median: the middle case. If mean and median differ a lot, a few big moves drive the mean.")
    print("- hit rate: share of positive views that beat the benchmark, or negative views that lagged it.")
    print("- ALL: every scored date, whatever the view. A view is only useful if it does better than ALL.")
    if small:
        print(f"- * fewer than {MIN_TRUSTED_N} cases. Too few to trust.")
    print("- Monthly views overlap in time, so the true number of independent cases is smaller than n.")
    print("- Prices are adjusted and exclude dividends. No trading costs are included.")
    if args.holdings:
        print("- These are stocks you chose to own. That is a biased sample, not the whole market.")
    if args.index:
        print("- Members are today's list. Stocks that left the index earlier are missing, which flatters results.")
    if args.analyst == "momentum":
        print(f"- Momentum ranks each stock only against the {len(ids)} stocks in this run.")


if __name__ == "__main__":
    main()
