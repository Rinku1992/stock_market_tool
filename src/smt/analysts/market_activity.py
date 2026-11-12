"""Market activity desk: price structure, liquidity, market beta. Code only, no LLM.

The direction rules below are simple and written out in full. They have NOT been
backtested yet, so confidence is capped at UNTESTED_CONFIDENCE and every report says so.
"""

from smt import indicators as ind
from smt.analysts.base import AnalystContext
from smt.data.prices import Bar, load_bars
from smt.factors import percentile, universe_scores
from smt.schema import AnalystKind, AnalystReport, Direction, Evidence, Horizon, HorizonView, neutral_views

UNIT = "market_activity"
BENCHMARK = "IDX:NIFTY50"
MIN_BARS = 200
UNTESTED_CONFIDENCE = 0.3
UNTESTED_NOTE = "direction rule is not backtested yet"
CRORE = 1e7
MIN_UNIVERSE = 10


def _report(ctx: AnalystContext, analyst_id: str, finding: str, views=None, evidence=None, could_not_check=None) -> AnalystReport:
    return AnalystReport(
        analyst_id=analyst_id, unit=UNIT, kind=AnalystKind.CODE, symbol=ctx.symbol, as_of=ctx.as_of,
        finding=finding, views=views or neutral_views(), evidence=evidence or [], could_not_check=could_not_check or [],
    )


def _evidence(instrument_id: str, bars: list[Bar]) -> list[Evidence]:
    return [Evidence(id=f"prices:{instrument_id}:{bars[-1].d}", source_type="price",
                     ref=f"prices_daily/{instrument_id}", as_of=bars[-1].d)]


def _save(ctx: AnalystContext, analyst_id: str, features: dict[str, float | None]) -> None:
    for name, value in features.items():
        if value is not None:
            ctx.store.put_feature(symbol=ctx.instrument_id, name=name, value=float(value), known_at=ctx.as_of,
                                  origin="code", source=analyst_id)


def _view(h: Horizon, up: bool, down: bool, size: float | None) -> HorizonView:
    direction = Direction.POSITIVE if up else Direction.NEGATIVE if down else Direction.NEUTRAL
    neutral = direction == Direction.NEUTRAL
    return HorizonView(horizon=h, direction=direction,
                       strength=0.0 if neutral else min(1.0, abs(size or 0.0) / 0.20),
                       confidence=0.0 if neutral else UNTESTED_CONFIDENCE)


def price_structure(ctx: AnalystContext) -> AnalystReport:
    """Trend and strength versus the Nifty 50.

    Short (3-5m): positive if close > 50-day avg > 200-day avg AND 3-month return beats the index.
                  negative if close < 50-day avg < 200-day avg AND 3-month return lags the index.
    Mid (6-12m):  positive if close > 200-day avg AND 6-month return > 0 and beats the index.
                  negative if close < 200-day avg AND 6-month return < 0 and lags the index.
    Long (2y):    always neutral. Price trend alone says little about a 2-year outcome.
    Without index data, the "beats / lags the index" part is skipped and noted.
    """
    aid = "price_structure"
    bars = load_bars(ctx.lake, ctx.instrument_id, ctx.as_of)
    if len(bars) < MIN_BARS:
        return _report(ctx, aid, f"Only {len(bars)} daily prices available. Need {MIN_BARS}.",
                       could_not_check=["trend: not enough price history"])
    closes = [b.close for b in bars]
    last, s50, s200 = closes[-1], ind.sma(closes, 50), ind.sma(closes, 200)
    r3, r6 = ind.pct_change(closes, ind.DAYS_3M), ind.pct_change(closes, ind.DAYS_6M)
    high, vol = ind.dist_from_high(closes), ind.annual_vol(closes)

    idx = [b.close for b in load_bars(ctx.lake, BENCHMARK, ctx.as_of)]
    i3, i6 = ind.pct_change(idx, ind.DAYS_3M), ind.pct_change(idx, ind.DAYS_6M)
    rel3 = r3 - i3 if r3 is not None and i3 is not None else None
    rel6 = r6 - i6 if r6 is not None and i6 is not None else None
    notes = [UNTESTED_NOTE]
    if rel3 is None or rel6 is None:
        notes.append("strength versus Nifty 50: no index prices loaded")

    def beats(rel):  # with no index data, do not block the signal
        return rel is None or rel > 0

    def lags(rel):
        return rel is None or rel < 0

    views = [
        _view(Horizon.SHORT, last > s50 > s200 and beats(rel3), last < s50 < s200 and lags(rel3), rel3 if rel3 is not None else r3),
        _view(Horizon.MID, r6 is not None and last > s200 and r6 > 0 and beats(rel6),
              r6 is not None and last < s200 and r6 < 0 and lags(rel6), rel6 if rel6 is not None else r6),
        _view(Horizon.LONG, False, False, None),
    ]
    _save(ctx, aid, {"sma_50": s50, "sma_200": s200, "ret_3m": r3, "ret_6m": r6, "rel_ret_3m": rel3,
                     "rel_ret_6m": rel6, "dist_52w_high": high, "vol_1y": vol})

    def pct(x):
        return "n/a" if x is None else f"{x:+.1%}"

    finding = (f"Close {last:.2f} on {bars[-1].d}. 50-day avg {s50:.2f}, 200-day avg {s200:.2f}. "
               f"3-month return {pct(r3)} (Nifty {pct(i3)}), 6-month {pct(r6)} (Nifty {pct(i6)}). "
               f"{pct(high)} from 52-week high. 1-year volatility {pct(vol).lstrip('+')}.")
    return _report(ctx, aid, finding, views, _evidence(ctx.instrument_id, bars), notes)


def liquidity(ctx: AnalystContext) -> AnalystReport:
    """How much trades per day. A constraint, not a signal, so the views stay neutral."""
    aid = "liquidity"
    bars = load_bars(ctx.lake, ctx.instrument_id, ctx.as_of)
    value = ind.median_traded_value([b.close for b in bars], [b.volume for b in bars], 60)
    if value is None:
        return _report(ctx, aid, f"Only {len(bars)} daily prices available. Need 60.",
                       could_not_check=["liquidity: not enough price history"])
    _save(ctx, aid, {"median_traded_value_60d": value})
    return _report(ctx, aid, f"Median daily traded value over the last 60 days: Rs {value / CRORE:,.0f} crore.",
                   evidence=_evidence(ctx.instrument_id, bars))


def market_beta(ctx: AnalystContext) -> AnalystReport:
    """How much of the stock's daily movement is just the market. Context, not a signal."""
    aid = "market_beta"
    bars = load_bars(ctx.lake, ctx.instrument_id, ctx.as_of)
    idx = load_bars(ctx.lake, BENCHMARK, ctx.as_of)
    out = ind.beta_r2({b.d: b.close for b in bars}, {b.d: b.close for b in idx})
    if out is None:
        why = "no index prices loaded" if not idx else "not enough overlapping history"
        return _report(ctx, aid, f"Beta not computed: {why}.", could_not_check=[f"market beta: {why}"])
    beta, r2 = out
    _save(ctx, aid, {"beta_1y": beta, "r2_1y": r2})
    return _report(ctx, aid, f"1-year beta to Nifty 50: {beta:.2f}. The index explains {r2:.0%} of the stock's daily moves.",
                   evidence=_evidence(ctx.instrument_id, bars))


def momentum(ctx: AnalystContext) -> AnalystReport:
    """Risk-adjusted 12-1 momentum, ranked against every loaded stock on the same date.

    Short and mid: top third of the ranking -> positive, bottom third -> negative, else neutral.
    Long: neutral. Needs at least MIN_UNIVERSE stocks with enough history to rank against.
    """
    aid = "momentum"
    scores = universe_scores(ctx.lake, ctx.as_of) if ctx.lake else {}
    me = scores.get(ctx.instrument_id) if ctx.instrument_id else None
    if me is None:
        return _report(ctx, aid, "Momentum not computed: needs about 13 months of recent daily prices.",
                       could_not_check=["momentum: not enough recent price history"])
    pct, rank, n = percentile(scores, ctx.instrument_id)
    _save(ctx, aid, {"mom_12_1": me["mom_12_1"], "mom_score": me["score"], "mom_percentile": pct})
    evidence = [Evidence(id=f"prices:{ctx.instrument_id}:{me['last_date']}", source_type="price",
                         ref=f"prices_daily/{ctx.instrument_id}", as_of=me["last_date"])]
    finding = (f"12-month return, skipping the last month: {me['mom_12_1']:+.1%}. "
               f"1-year volatility {me['vol_1y']:.1%}. Risk-adjusted score {me['score']:.2f}.")
    if n < MIN_UNIVERSE:
        return _report(ctx, aid, f"{finding} Only {n} stocks loaded to rank against.", evidence=evidence,
                       could_not_check=[f"momentum rank: only {n} stocks loaded, need {MIN_UNIVERSE}"])
    finding += f" Rank {rank} of {n} loaded stocks (percentile {pct:.0%})."
    up, down, size = pct >= 2 / 3, pct <= 1 / 3, (pct - 0.5) * 2 * 0.20
    views = [_view(Horizon.SHORT, up, down, size), _view(Horizon.MID, up, down, size), _view(Horizon.LONG, False, False, None)]
    notes = [UNTESTED_NOTE, "rank is against the stocks loaded, not the whole market"]
    return _report(ctx, aid, finding, views, evidence, notes)
