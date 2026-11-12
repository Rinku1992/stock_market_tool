"""Cross-sectional factors: computed for every stock in the universe on a date, then ranked.

Momentum here is the 12-1 form common in research: the return from 12 months ago to 1 month
ago. The last month is skipped because very recent moves tend to partly reverse.
It is divided by 1-year volatility, so a calm steady riser ranks above a wild one.
"""

from datetime import date

from smt import indicators as ind
from smt.data.lake import Lake
from smt.data.prices import load_bars, universe_ids

SKIP = 21  # trading days, about one month
LOOKBACK = 252  # trading days, about one year
MAX_STALE_DAYS = 10  # a stock whose last price is older than this is left out (suspended, delisted)


def momentum_12_1(closes: list[float]) -> float | None:
    if len(closes) <= LOOKBACK:
        return None
    return closes[-1 - SKIP] / closes[-1 - LOOKBACK] - 1


def risk_adjusted_momentum(closes: list[float]) -> float | None:
    m, v = momentum_12_1(closes), ind.annual_vol(closes)
    if m is None or not v:
        return None
    return m / v


def universe_scores(lake: Lake, as_of: date) -> dict[str, dict]:
    """Momentum factors for every stock in the universe that has enough recent history on as_of."""
    ids = universe_ids(lake)  # may reset the memo, so read the memo after this
    memo = lake.__dict__.setdefault("_factor_memo", {})
    if as_of in memo:
        return memo[as_of]
    out = {}
    for iid in ids:
        bars = load_bars(lake, iid, as_of)
        if not bars or (as_of - bars[-1].d).days > MAX_STALE_DAYS:
            continue
        closes = [b.close for b in bars]
        score = risk_adjusted_momentum(closes)
        if score is not None:
            out[iid] = {"mom_12_1": momentum_12_1(closes), "vol_1y": ind.annual_vol(closes),
                        "score": score, "last_date": bars[-1].d}
    memo[as_of] = out
    return out


def percentile(scores: dict[str, dict], instrument_id: str) -> tuple[float, int, int]:
    """(percentile 0..1 with 1 = best, rank with 1 = best, universe size)."""
    ranked = sorted(scores, key=lambda k: scores[k]["score"])
    n, i = len(ranked), ranked.index(instrument_id)
    return (i / (n - 1) if n > 1 else 0.5), n - i, n
