"""Backtest engine. Plain code: no LLM, no LangGraph.

For each month-end date and each stock, it asks an analyst for its view using only data
known on that date, then measures what the stock did afterwards versus a benchmark:
- "universe" (default with 10+ stocks): the median forward return of all stocks in the run on
  that same date. Fair when the stock list as a whole beat the index.
- "nifty": the Nifty 50.

The analyst can never see the future: it reads prices through load_bars(as_of).
Only this engine looks forward, and only to score the view.
"""

from collections import defaultdict
from statistics import median
from dataclasses import dataclass
from datetime import date

from smt.analysts.base import Analyst, AnalystContext
from smt.analysts.market_activity import BENCHMARK
from smt.data.lake import Lake
from smt.data.prices import cache_bars, load_bars
from smt.schema import Direction, Horizon

# Trading days used to score each horizon (midpoints of the ranges).
FWD_DAYS = {Horizon.SHORT: 84, Horizon.MID: 189, Horizon.LONG: 500}  # ~4 months, ~9 months, ~2 years
MIN_TRUSTED_N = 30
MIN_UNIVERSE_BENCH = 10  # stocks needed on a date to use their median as the benchmark
BENCHMARKS = ("universe", "nifty")
_FAR = date(2200, 1, 1)


@dataclass(frozen=True)
class Obs:
    instrument_id: str
    as_of: date
    horizon: Horizon
    direction: Direction
    fwd_return: float
    fwd_bench_return: float

    @property
    def excess(self) -> float:
        return self.fwd_return - self.fwd_bench_return


@dataclass(frozen=True)
class Row:
    horizon: Horizon
    view: str  # positive / negative / neutral / ALL
    n: int
    n_stocks: int
    mean_excess: float
    median_excess: float
    hit_rate: float | None  # share of views that were right. None for neutral and ALL.


class _NullStore:
    """Backtests do not write features."""

    def put_feature(self, **kwargs) -> None:
        pass


def month_ends(dates: list[date]) -> list[date]:
    """Last trading day of each month, for months that are complete in the data."""
    return [a for a, b in zip(dates, dates[1:]) if (a.year, a.month) != (b.year, b.month)]


def _fwd(bars, i: int, n: int) -> float | None:
    return bars[i + n].close / bars[i].close - 1 if i + n < len(bars) else None


def run_backtest(lake: Lake, instrument_ids: list[str], analyst: Analyst, benchmark: str = BENCHMARK,
                 start: date | None = None, end: date | None = None, vs: str = "nifty") -> list[Obs]:
    if vs not in BENCHMARKS:
        raise ValueError(f"vs must be one of {BENCHMARKS}")
    cache_bars(lake, [*instrument_ids, benchmark])
    idx = load_bars(lake, benchmark, _FAR)
    if not idx:
        raise ValueError(f"no prices loaded for benchmark {benchmark}")
    idx_pos = {b.d: i for i, b in enumerate(idx)}
    evals = [d for d in month_ends([b.d for b in idx]) if (start is None or d >= start) and (end is None or d <= end)]
    series = {iid: load_bars(lake, iid, _FAR) for iid in instrument_ids}
    pos = {iid: {b.d: i for i, b in enumerate(bars)} for iid, bars in series.items()}

    # The benchmark return for each date and horizon, worked out once.
    bench: dict[tuple[date, Horizon], float | None] = {}
    for d in evals:
        for h, n in FWD_DAYS.items():
            if vs == "nifty":
                bench[(d, h)] = _fwd(idx, idx_pos[d], n)
            else:
                vals = [r for iid in instrument_ids if d in pos[iid]
                        for r in [_fwd(series[iid], pos[iid][d], n)] if r is not None]
                bench[(d, h)] = median(vals) if len(vals) >= MIN_UNIVERSE_BENCH else None

    store, obs = _NullStore(), []
    for iid in instrument_ids:
        bars = series[iid]
        for d in evals:
            if d not in pos[iid]:
                continue
            report = analyst(AnalystContext(symbol=iid, as_of=d, store=store, lake=lake, instrument_id=iid))
            if not report.evidence:  # the analyst could not run (too little history), so there is no view to score
                continue
            for v in report.views:
                r, b = _fwd(bars, pos[iid][d], FWD_DAYS[v.horizon]), bench[(d, v.horizon)]
                if r is None or b is None:
                    continue  # the future needed to score this view has not happened yet
                obs.append(Obs(iid, d, v.horizon, v.direction, r, b))
    return obs


def summarize(obs: list[Obs]) -> list[Row]:
    groups: dict[tuple[Horizon, str], list[Obs]] = defaultdict(list)
    for o in obs:
        groups[(o.horizon, o.direction.value)].append(o)
        groups[(o.horizon, "ALL")].append(o)
    rows = []
    for h in Horizon:
        for view in ("positive", "negative", "neutral", "ALL"):
            g = groups.get((h, view))
            if not g:
                continue
            hit = None
            if view == "positive":
                hit = sum(o.excess > 0 for o in g) / len(g)
            elif view == "negative":
                hit = sum(o.excess < 0 for o in g) / len(g)
            ex = [o.excess for o in g]
            rows.append(Row(h, view, len(g), len({o.instrument_id for o in g}), sum(ex) / len(g), median(ex), hit))
    return rows
