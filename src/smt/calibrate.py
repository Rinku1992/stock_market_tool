"""Calibration officer (Track record office). Plain code: no LLM, no LangGraph.

Sets each analyst's desk weight from measured results, using rules fixed in DECISIONS.md
before they were run on real data:

    edge   = median excess of positive views - median excess of negative views
    weight > 0 only if: >= 300 positive and >= 300 negative cases; edge > 0 in each of 3 equal
             time periods; edge beats >= 95% of 500 within-date shuffles (seed 0).
    weight = edge / (edge + 0.02), capped at 1. Otherwise 0.

Market regime (Nifty 50 above / below its 200-day average) is reported only, not used in weights.

    python -m smt.calibrate market_activity --index IDX:NIFTY100           # print the table only
    python -m smt.calibrate market_activity --index IDX:NIFTY100 --write   # save a new weights version
"""

import argparse
import json
import os
import random
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from statistics import median

from smt.analysts.market_activity import BENCHMARK
from smt.backtest import MIN_UNIVERSE_BENCH, Obs, _FAR, run_backtest
from smt.schema import Direction, Horizon

MIN_CASES = 300
PERIODS = 3
SHUFFLES = 500
SHUFFLE_PASS = 0.95
SEED = 0
HALF_WEIGHT_EDGE = 0.02
REGIME_DAYS = 200
WEIGHTS_DIR = Path(__file__).parent / "desks" / "weights"


@dataclass
class Cell:
    """One analyst, one horizon."""
    n_pos: int
    n_neg: int
    edge: float | None
    period_edges: list[float | None]
    stable: bool
    beat_share: float | None  # share of shuffled edges the real edge is above
    weight: float
    why: str
    regime: dict[str, tuple[int, float | None]] = field(default_factory=dict)  # regime -> (cases, edge)


def edge(obs: list[Obs]) -> float | None:
    pos = [o.excess for o in obs if o.direction == Direction.POSITIVE]
    neg = [o.excess for o in obs if o.direction == Direction.NEGATIVE]
    return median(pos) - median(neg) if pos and neg else None


def period_edges(obs: list[Obs], k: int = PERIODS) -> list[float | None]:
    """Edge in k consecutive periods with equal numbers of dates."""
    dates = sorted({o.as_of for o in obs})
    if len(dates) < k:
        return [None] * k
    size, out = len(dates) / k, []
    for i in range(k):
        part = set(dates[round(i * size):round((i + 1) * size)])
        out.append(edge([o for o in obs if o.as_of in part]))
    return out


def shuffle_beat_share(obs: list[Obs], real: float, n: int = SHUFFLES, seed: int = SEED) -> float:
    """Share of within-date shuffles whose edge the real edge is strictly above."""
    rng = random.Random(seed)
    by_date: dict[date, list[Obs]] = defaultdict(list)
    for o in obs:
        by_date[o.as_of].append(o)
    groups = [([o.excess for o in g], [o.direction for o in g]) for _, g in sorted(by_date.items())]
    beaten = 0
    for _ in range(n):
        pos, neg = [], []
        for excess, dirs in groups:
            d = dirs[:]
            rng.shuffle(d)
            for x, v in zip(excess, d):
                if v == Direction.POSITIVE:
                    pos.append(x)
                elif v == Direction.NEGATIVE:
                    neg.append(x)
        if real > median(pos) - median(neg):
            beaten += 1
    return beaten / n


def assess(obs: list[Obs], regime: dict[date, str] | None = None) -> Cell:
    """Apply the fixed rules to the scored views of one analyst for one horizon."""
    n_pos = sum(o.direction == Direction.POSITIVE for o in obs)
    n_neg = sum(o.direction == Direction.NEGATIVE for o in obs)
    e = edge(obs)
    regimes = {}
    if regime:
        for name in ("above", "below"):
            sub = [o for o in obs if regime.get(o.as_of) == name]
            regimes[name] = (len(sub), edge(sub))
    if n_pos == 0 and n_neg == 0:
        return Cell(0, 0, None, [None] * PERIODS, False, None, 0.0, "Gives no direction (context only).", regimes)
    pe = period_edges(obs)
    stable = all(x is not None and x > 0 for x in pe)
    enough = n_pos >= MIN_CASES and n_neg >= MIN_CASES
    beat = shuffle_beat_share(obs, e) if e is not None else None
    fails = []
    if not enough:
        fails.append(f"too few cases ({n_pos} positive, {n_neg} negative; need {MIN_CASES} each)")
    if e is None or e <= 0:
        fails.append("edge not above 0" if e is not None else "no edge: positive or negative views missing")
    if not stable:
        fails.append("edge not above 0 in all 3 periods (" + ", ".join(_pt(x) for x in pe) + ")")
    if beat is None or beat < SHUFFLE_PASS:
        fails.append("shuffle test failed" + (f" (beats {beat:.0%} of shuffles, needs {SHUFFLE_PASS:.0%})" if beat is not None else ""))
    if fails:
        return Cell(n_pos, n_neg, e, pe, stable, beat, 0.0, "Weight 0: " + "; ".join(fails) + ".", regimes)
    w = min(1.0, e / (e + HALF_WEIGHT_EDGE))
    why = (f"Edge {_pt(e)} with {n_pos} positive and {n_neg} negative cases, above 0 in all 3 periods "
           f"({', '.join(_pt(x) for x in pe)}), beats {beat:.0%} of shuffles.")
    return Cell(n_pos, n_neg, e, pe, stable, beat, round(w, 3), why, regimes)


def _pt(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:+.1f} pts"


def scored_span(obs: list[Obs]) -> dict[Horizon, tuple[date, date] | None]:
    """First and last date with a scored positive or negative view, per horizon.

    Neutral views are left out: they do not enter the edge. Each horizon has its own end, because a
    longer horizon needs more of the future to score a view.
    """
    out = {}
    for h in Horizon:
        d = [o.as_of for o in obs if o.horizon == h and o.direction != Direction.NEUTRAL]
        out[h] = (min(d), max(d)) if d else None
    return out


def describe_span(span: dict[Horizon, tuple[date, date] | None]) -> str:
    return ", ".join(f"{h.value} {v[0]} to {v[1]}" if v else f"{h.value} none" for h, v in span.items())


def regime_by_date(bars) -> dict[date, str]:
    """'above' / 'below' the 200-day average close, for each date with enough history."""
    out, closes = {}, [b.close for b in bars]
    for i in range(REGIME_DAYS - 1, len(bars)):
        avg = sum(closes[i - REGIME_DAYS + 1:i + 1]) / REGIME_DAYS
        out[bars[i].d] = "above" if closes[i] > avg else "below"
    return out


def calibrate(analysts: dict, run, regime: dict[date, str]) -> dict[str, dict[Horizon, Cell]]:
    """analysts: name -> function. run(analyst) -> list[Obs]."""
    out = {}
    for name, fn in analysts.items():
        obs = run(fn)
        out[name] = {h: assess([o for o in obs if o.horizon == h], regime) for h in Horizon}
    return out


def weights_doc(unit: str, results: dict[str, dict[Horizon, Cell]], version: str, source: str) -> dict:
    return {
        "unit": unit, "version": version, "source": source,
        "weights": {a: {h.value: c.weight for h, c in cells.items()} for a, cells in results.items()},
        "why": {a: " ".join(f"{h.value}: {c.why}" for h, c in cells.items()) for a, cells in results.items()},
    }


def _next_version(version: str, folder: Path, unit: str, current: str | None) -> str:
    """A second version on the same day gets .2, .3, ... so every version name is unique."""
    taken = {current} | {json.loads(p.read_text()).get("version") for p in (folder / "history").glob(f"{unit}_*.json")}
    if version not in taken:
        return version
    i = 2
    while f"{version}.{i}" in taken:
        i += 1
    return f"{version}.{i}"


def write_weights(unit: str, doc: dict, folder: Path = WEIGHTS_DIR) -> Path:
    """Save a new version. The old file is kept in history/ and never overwritten."""
    path = folder / f"{unit}.json"
    if path.exists():
        old = json.loads(path.read_text())
        doc = {**doc, "version": _next_version(doc["version"], folder, unit, old.get("version"))}
        hist = folder / "history"
        hist.mkdir(parents=True, exist_ok=True)
        target, i = hist / f"{unit}_{old.get('version', 'none')}.json", 1
        while target.exists():
            i += 1
            target = hist / f"{unit}_{old.get('version', 'none')}_{i}.json"
        shutil.copy2(path, target)
    path.write_text(json.dumps(doc, indent=2) + "\n")
    return path


def print_table(results: dict[str, dict[Horizon, Cell]]) -> None:
    print(f"{'analyst':16} {'horizon':7} {'pos':>5} {'neg':>5} {'edge':>9} {'stable':>6} {'shuffle':>8} {'weight':>6}"
          f"   {'edge, Nifty above 200d':>23} {'below 200d':>18}")
    for a, cells in results.items():
        for h, c in cells.items():
            sh = "-" if c.beat_share is None else f"{c.beat_share:.0%} " + ("ok" if c.beat_share >= SHUFFLE_PASS else "no")
            reg = [f"{_pt(c.regime[k][1])} (n={c.regime[k][0]})" if k in c.regime else "-" for k in ("above", "below")]
            print(f"{a:16} {h.value:7} {c.n_pos:>5} {c.n_neg:>5} {_pt(c.edge):>9} {'yes' if c.stable else 'no':>6} "
                  f"{sh:>8} {c.weight:>6.3f}   {reg[0]:>23} {reg[1]:>18}")


def main(argv: list[str] | None = None) -> None:
    from dotenv import load_dotenv

    from smt.analysts import DESKS
    from smt.data.lake import Lake
    from smt.data.members import index_members
    from smt.data.prices import load_bars

    load_dotenv()
    p = argparse.ArgumentParser(prog="smt.calibrate")
    p.add_argument("unit", choices=sorted(DESKS))
    p.add_argument("--index", required=True, help="universe to test on, e.g. IDX:NIFTY100")
    p.add_argument("--write", action="store_true", help="save a new weights version (old one kept in history/)")
    args = p.parse_args(argv)
    lake = Lake(os.environ.get("SMT_LAKE_PATH", "data/lake"))
    ids = [i for i, _ in index_members(lake, args.index)]
    if len(ids) < MIN_UNIVERSE_BENCH:
        p.error(f"need at least {MIN_UNIVERSE_BENCH} stocks with the index list loaded; found {len(ids)}")
    analysts = {fn.__name__: fn for fn in DESKS[args.unit]}
    scored: list[Obs] = []

    def run(fn):
        obs = run_backtest(lake, ids, fn, vs="universe")
        scored.extend(obs)
        return obs

    regime = regime_by_date(load_bars(lake, BENCHMARK, _FAR))
    results = calibrate(analysts, run, regime)
    source = (f"Calibration officer: {args.index} ({len(ids)} stocks), monthly views scored against the median stock "
              f"on the same date; positive or negative views scored {describe_span(scored_span(scored))}; "
              f"rules in DECISIONS.md (13 Nov 2026)")
    print(f"\nCalibration: {args.unit} | {source}\n")
    print_table(results)
    print("\nHow to read this")
    print("- edge: median excess of positive views minus median excess of negative views, in percentage points.")
    print(f"- weight above 0 needs {MIN_CASES}+ positive and negative cases, edge above 0 in all {PERIODS} time periods (stable),")
    print(f"  and the edge beating {SHUFFLE_PASS:.0%} of {SHUFFLES} within-date shuffles. Regime columns are for information only.")
    print("- regime columns: edge on dates when the Nifty 50 was above or below its 200-day average. n counts all scored")
    print("  views, neutral included. For information only: not used in the weights yet.")
    for a, cells in results.items():
        print(f"- {a}: " + " ".join(f"{h.value}: {c.why}" for h, c in cells.items()))
    if args.write:
        path = write_weights(args.unit, weights_doc(args.unit, results, date.today().isoformat(), source))
        version = json.loads(path.read_text())["version"]
        print(f"\nSaved weights version {version} to {path}. The old version is in {path.parent / 'history'}.")
    else:
        print("\nNot saved. Run again with --write to save these weights.")


if __name__ == "__main__":
    main()
