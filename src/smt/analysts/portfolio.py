"""Portfolio and position desk. Code only, no LLM. Facts only: every view is neutral.

Analysts: position_size, overlap, holding_context, thesis_tracker. Each reports "not held" (and nothing else)
when the stock being run is not in the account's latest stored holdings.

Privacy: holdings are read by account alias (settings file), never by the broker client id, which is never
stored. Holdings data stays in data/ (not in git); tests use made-up holdings.

Inputs: the latest stored holdings on or before as_of (quantity, average price, holdings date) and stored daily
closes. Market value = quantity x latest close. A holding with no stored price is "not covered": listed, and left
out of the maths.

Maths (pure functions below):
- weight = market value / total market value of covered holdings; top-five share; sector weights (industry from
  the index member list; none stored: "sector not known").
- daily returns over the last 250 trading days on dates common to all covered holdings (at least 120);
  sample covariance matrix S; portfolio variance w'Sw, volatility sqrt(252 w'Sw).
- risk share of stock i = w_i (S w)_i / (w'Sw); the shares sum to 100%.
- correlation of the stock with the rest of the portfolio (the other holdings, weights rescaled), its three most
  correlated holdings; portfolio beta to the Nifty 50 = cov(p, m) / var(m) on the same dates.
"""

import json
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from math import sqrt
from pathlib import Path

from smt.analysts.base import AnalystContext
from smt.schema import AnalystKind, AnalystReport, Evidence, neutral_views

UNIT = "portfolio"
NOT_HELD = "not held"
SETTINGS = Path(__file__).resolve().parents[1] / "assumptions" / "portfolio_limits.json"
BENCHMARK = "IDX:NIFTY50"
WINDOW, MIN_DAYS, YEAR = 250, 120, 252
NO_BUY_DATES = "not available: stored holdings carry no purchase dates (needs trade history)"
LATER = "Checking the thesis against the facts is for later (needs the LLM reviewers)."


# ---------------------------------------------------------------- pure maths

def weights(values: list[float]) -> list[float]:
    total = sum(values)
    return [v / total for v in values]


def top_share(ws: list[float], n: int = 5) -> float:
    return sum(sorted(ws, reverse=True)[:n])


def sector_weights(sectors: list[str | None], ws: list[float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for s, w in zip(sectors, ws):
        k = s or "sector not known"
        out[k] = out.get(k, 0.0) + w
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def limit_text(value: float, limit: float) -> str:
    return "inside the limit" if value <= limit else f"above the {limit:.0%} limit"


def returns(closes: list[float]) -> list[float]:
    return [b / a - 1 for a, b in zip(closes, closes[1:])]


def _mean(x: list[float]) -> float:
    return sum(x) / len(x)


def cov(x: list[float], y: list[float]) -> float:
    mx, my = _mean(x), _mean(y)
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (len(x) - 1)


def corr(x: list[float], y: list[float]) -> float | None:
    vx, vy = cov(x, x), cov(y, y)
    return cov(x, y) / sqrt(vx * vy) if vx > 0 and vy > 0 else None


def cov_matrix(rets: list[list[float]]) -> list[list[float]]:
    return [[cov(a, b) for b in rets] for a in rets]


def port_variance(S: list[list[float]], w: list[float]) -> float:
    return sum(w[i] * S[i][j] * w[j] for i in range(len(w)) for j in range(len(w)))


def risk_shares(S: list[list[float]], w: list[float]) -> list[float]:
    var = port_variance(S, w)
    Sw = [sum(S[i][j] * w[j] for j in range(len(w))) for i in range(len(w))]
    return [w[i] * Sw[i] / var for i in range(len(w))]


def combine(rets: list[list[float]], w: list[float]) -> list[float]:
    return [sum(wi * r[t] for wi, r in zip(w, rets)) for t in range(len(rets[0]))]


def annual_vol(daily_var: float) -> float:
    return sqrt(daily_var * YEAR)


def beta(p: list[float], m: list[float]) -> float | None:
    vm = cov(m, m)
    return cov(p, m) / vm if vm > 0 else None


# ---------------------------------------------------------------- the book (reads, once per run date)

def load_settings(path: Path = SETTINGS) -> dict:
    return json.loads(path.read_text())


@dataclass
class Holding:
    iid: str
    symbol: str
    qty: float
    avg: float
    close: float | None = None
    close_date: date | None = None
    sector: str | None = None
    bars: dict = field(default_factory=dict)  # trade date -> close

    @property
    def value(self) -> float | None:
        return None if self.close is None else self.qty * self.close


@dataclass
class Book:
    account: str
    holdings_date: date
    covered: list[Holding]
    not_covered: list[Holding]
    weights: list[float]
    settings: dict
    risk: dict | None = None  # filled by risk_view()
    risk_why: str | None = None

    def find(self, iid: str) -> Holding | None:
        return next((h for h in self.covered + self.not_covered if h.iid == iid), None)

    def index(self, iid: str) -> int | None:
        return next((i for i, h in enumerate(self.covered) if h.iid == iid), None)


def _read_holdings(lake, account: str, as_of: date) -> tuple[date | None, list[tuple]]:
    if lake is None or not lake.has("holdings"):
        return None, []
    src = lake.read_sql("holdings")
    got = lake.query(f"SELECT max(as_of) FROM {src} WHERE account = ? AND as_of <= ?", [account, as_of])
    d = got[0][0] if got else None
    if d is None:
        return None, []
    rows = lake.query(f"SELECT instrument_id, coalesce(source_ref, instrument_id), quantity, avg_price FROM {src} "
                      "WHERE account = ? AND as_of = ? ORDER BY instrument_id", [account, d])
    return d, rows


def _book(lake, as_of: date, settings_path: Path) -> Book | None:
    from smt.data.prices import load_bars
    from smt.router import _industry

    st = load_settings(settings_path)
    account = st["account_alias"]
    d, rows = _read_holdings(lake, account, as_of)
    if d is None:
        return None
    covered, missing = [], []
    for iid, sym, qty, avg in rows:
        h = Holding(iid, sym, qty, avg, sector=_industry(lake, iid))
        bars = load_bars(lake, iid, as_of)
        if bars:
            h.close, h.close_date = bars[-1].close, bars[-1].d
            h.bars = {b.d: b.close for b in bars}
            covered.append(h)
        else:
            missing.append(h)
    ws = weights([h.value for h in covered]) if covered else []
    book = Book(account, d, covered, missing, ws, st)
    book.risk, book.risk_why = risk_view(book, {b.d: b.close for b in load_bars(lake, BENCHMARK, as_of)})
    return book


@lru_cache(maxsize=8)
def _cached(lake_id: int, lake_ref, as_of: date, settings_path: Path) -> Book | None:
    return _book(lake_ref, as_of, settings_path)


def get_book(lake, as_of: date, settings_path: Path = SETTINGS) -> Book | None:
    """The book for one run date (computed once, then reused by the four analysts and by every stock's run)."""
    return _cached(id(lake), lake, as_of, settings_path)


def risk_view(book: Book, nifty: dict) -> tuple[dict | None, str | None]:
    """Covariance-based figures on dates common to all covered holdings; or None and the reason."""
    if len(book.covered) < 2:
        return None, f"only {len(book.covered)} covered holding(s); risk shares need at least 2"
    common = set.intersection(*(set(h.bars) for h in book.covered))
    days = sorted(common)[-(WINDOW + 1):]
    n = len(days) - 1
    if n < MIN_DAYS:
        return None, f"only {max(n, 0)} daily returns on dates common to all covered holdings (needs {MIN_DAYS})"
    rets = [returns([h.bars[d] for d in days]) for h in book.covered]
    S = cov_matrix(rets)
    w = book.weights
    var = port_variance(S, w)
    out = {"days": n, "start": days[0], "end": days[-1], "vol": annual_vol(var), "shares": risk_shares(S, w),
           "rets": rets, "beta": None, "nifty_vol": None}
    port = combine(rets, w)
    if all(d in nifty for d in days):
        m = returns([nifty[d] for d in days])
        out["beta"], out["nifty_vol"] = beta(port, m), annual_vol(cov(m, m))
    return out, None


def corr_with_rest(book: Book, i: int) -> float | None:
    rets, w = book.risk["rets"], book.weights
    rest = [x for j, x in enumerate(w) if j != i]
    total = sum(rest)
    if total <= 0:
        return None
    rest_rets = combine([r for j, r in enumerate(rets) if j != i], [x / total for x in rest])
    return corr(rets[i], rest_rets)


def top_correlated(book: Book, i: int, n: int = 3) -> list[tuple[str, float]]:
    rets = book.risk["rets"]
    pairs = [(h.symbol, corr(rets[i], rets[j])) for j, h in enumerate(book.covered) if j != i]
    return sorted([p for p in pairs if p[1] is not None], key=lambda p: -p[1])[:n]


# ---------------------------------------------------------------- reports

def _report(ctx, aid, finding, evidence=None, gaps=None) -> AnalystReport:
    return AnalystReport(analyst_id=aid, unit=UNIT, kind=AnalystKind.CODE, symbol=ctx.symbol, as_of=ctx.as_of,
                         finding=finding, views=neutral_views(), evidence=evidence or [], could_not_check=gaps or [])


def _save(ctx, aid, feats: dict, texts: dict | None = None) -> None:
    for k, v in feats.items():
        if v is not None:
            ctx.store.put_feature(symbol=ctx.instrument_id, name=k, value=float(v), known_at=ctx.as_of, origin="code",
                                  source=aid)
    for k, v in (texts or {}).items():
        ctx.store.put_feature(symbol=ctx.instrument_id, name=k, text_value=v, known_at=ctx.as_of, origin="code",
                              source=aid)


def _evidence(book: Book, h: Holding) -> list[Evidence]:
    ev = [Evidence(id=f"holdings:{book.account}:{book.holdings_date}", source_type="holdings",
                   ref=f"holdings/{book.account}/{book.holdings_date}", as_of=book.holdings_date)]
    if h.close_date:
        ev.append(Evidence(id=f"prices:{h.iid}:{h.close_date}", source_type="price", ref=f"prices_daily/{h.iid}",
                           as_of=h.close_date))
    return ev


def _held(ctx, aid) -> tuple[Book | None, Holding | None, AnalystReport | None]:
    book = get_book(ctx.lake, ctx.as_of) if ctx.lake is not None else None
    h = book.find(ctx.instrument_id) if book and ctx.instrument_id else None
    if h is None:
        return None, None, _report(ctx, aid, NOT_HELD)
    return book, h, None


def _not_covered_line(book: Book) -> str:
    if not book.not_covered:
        return ""
    return (" Not covered (no stored price, left out of the maths): "
            + ", ".join(h.symbol for h in book.not_covered) + ".")


def position_size(ctx: AnalystContext) -> AnalystReport:
    aid = "position_size"
    book, h, out = _held(ctx, aid)
    if out:
        return out
    lim = book.settings["limits"]
    label = book.settings["label"]
    dates = sorted({x.close_date for x in book.covered})
    head = (f"Account {book.account}: holdings of {book.holdings_date}; market value = quantity x latest close "
            f"(closes {dates[0]} to {dates[-1]})." if dates else
            f"Account {book.account}: holdings of {book.holdings_date}; no holding has a stored price.")
    parts = [head, f"{len(book.covered) + len(book.not_covered)} holdings, {len(book.covered)} covered by stored prices."]
    if not book.covered:
        return _report(ctx, aid, " ".join(parts) + _not_covered_line(book), _evidence(book, h))
    sw = sector_weights([x.sector for x in book.covered], book.weights)
    top5 = top_share(book.weights)
    i = book.index(h.iid)
    feats = {"pf_holdings": len(book.covered) + len(book.not_covered), "pf_covered": len(book.covered),
             "pf_top5_share": top5}
    feats.update({f"pf_sector_w:{k}": v for k, v in sw.items()})
    texts = {}
    if i is None:
        parts.append(f"{h.symbol} is held but not covered: no stored price, so it is left out of the maths.")
    else:
        w = book.weights[i]
        rank = sorted(book.weights, reverse=True).index(w) + 1
        sec = h.sector or "sector not known"
        parts.append(f"{h.symbol}: weight {w:.1%} (rank {rank} of {len(book.covered)}); single stock limit "
                     f"{lim['single_stock']:.0%} ({label}): {limit_text(w, lim['single_stock'])}.")
        parts.append(f"Sector {sec}: {sw[sec]:.1%}" + (f"; single sector limit {lim['single_sector']:.0%}: "
                     f"{limit_text(sw[sec], lim['single_sector'])}." if h.sector else "; no sector limit check."))
        feats.update({"pf_weight": w, "pf_rank": rank, "pf_sector_weight": sw[sec]})
        texts["pf_sector"] = sec
    parts.append(f"Top five holdings: {top5:.1%} together; top five limit {lim['top_five']:.0%}: "
                 f"{limit_text(top5, lim['top_five'])}.")
    parts.append("Sector weights: " + ", ".join(f"{k} {v:.1%}" for k, v in sw.items()) + ".")
    breaches = [f"{x.symbol} {wx:.1%} above the single stock limit {lim['single_stock']:.0%}"
                for x, wx in zip(book.covered, book.weights) if wx > lim["single_stock"]]
    breaches += [f"sector {k} {v:.1%} above the single sector limit {lim['single_sector']:.0%}"
                 for k, v in sw.items() if k != "sector not known" and v > lim["single_sector"]]
    if top5 > lim["top_five"]:
        breaches.append(f"top five {top5:.1%} above the top five limit {lim['top_five']:.0%}")
    parts.append(("Limit breached: " + "; ".join(breaches) + ".") if breaches else "No limit breached.")
    feats["pf_breaches"] = len(breaches)
    _save(ctx, aid, feats, texts)
    gaps = [f"not covered: {x.symbol} (no stored price)" for x in book.not_covered]
    return _report(ctx, aid, " ".join(parts) + _not_covered_line(book), _evidence(book, h), gaps)


def overlap(ctx: AnalystContext) -> AnalystReport:
    aid = "overlap"
    book, h, out = _held(ctx, aid)
    if out:
        return out
    i = book.index(h.iid)
    if i is None:
        return _report(ctx, aid, f"Overlap not computed: {h.symbol} has no stored price.",
                       gaps=[f"overlap: {h.symbol} not covered"])
    if book.risk is None:
        return _report(ctx, aid, f"Overlap not computed: {book.risk_why}.", gaps=[f"overlap: {book.risk_why}"])
    r = book.risk
    w, share = book.weights[i], r["shares"][i]
    parts = [f"Daily returns over the last {r['days']} trading days common to all {len(book.covered)} covered "
             f"holdings ({r['start']} to {r['end']})."]
    if r["beta"] is not None:
        parts.append(f"Portfolio volatility {r['vol']:.1%} a year (Nifty 50 {r['nifty_vol']:.1%}); portfolio beta to "
                     f"the Nifty 50 {r['beta']:.2f}.")
    else:
        parts.append(f"Portfolio volatility {r['vol']:.1%} a year; Nifty 50 prices missing on some of these dates, so no "
                     "beta.")
    parts.append(f"{h.symbol}: money share {w:.1%}, risk share {share:.1%} (risk share = weight x (covariance matrix x "
                 "weights) / portfolio variance; all risk shares add up to 100%).")
    c = corr_with_rest(book, i)
    tops = top_correlated(book, i)
    parts.append(f"Correlation with the rest of the portfolio {c:.2f}." if c is not None else
                 "Correlation with the rest of the portfolio not computed.")
    if tops:
        parts.append("Most correlated holdings: " + ", ".join(f"{s} {v:.2f}" for s, v in tops) + ".")
    _save(ctx, aid, {"pf_risk_share": share, "pf_money_share": w, "pf_port_vol": r["vol"], "pf_port_beta": r["beta"],
                     "pf_nifty_vol": r["nifty_vol"], "pf_corr_rest": c})
    return _report(ctx, aid, " ".join(parts), _evidence(book, h))


def holding_context(ctx: AnalystContext) -> AnalystReport:
    aid = "holding_context"
    book, h, out = _held(ctx, aid)
    if out:
        return out
    invested = h.qty * h.avg
    s = (f"{h.symbol}: quantity {h.qty:,.0f}, average purchase price Rs {h.avg:,.2f}, money invested Rs {invested:,.0f} "
         f"(holdings of {book.holdings_date}).")
    feats = {"pf_invested": invested}
    if h.value is not None:
        gain = h.value - invested
        s += (f" Market value Rs {h.value:,.0f} (close Rs {h.close:,.2f} on {h.close_date}); gain Rs {gain:,.0f} "
              f"({gain / invested:+.1%})." if invested else f" Market value Rs {h.value:,.0f}.")
        feats.update({"pf_market_value": h.value, "pf_gain": gain, "pf_gain_pct": gain / invested if invested else None})
    else:
        s += " Market value not available: no stored price."
    s += f" Holding period and the date it turns long-term for tax: {NO_BUY_DATES}."
    _save(ctx, aid, feats)
    return _report(ctx, aid, s, _evidence(book, h), [f"holding period: {NO_BUY_DATES}"])


def latest_thesis(lake, iid: str, as_of: date):
    if lake is None or not iid or not lake.has("theses"):
        return None
    from smt.data.financials import market_close_utc
    got = lake.query(f"SELECT text, epoch(published_at) FROM {lake.read_sql('theses')} WHERE instrument_id = ? "
                     "AND published_at <= ? ORDER BY published_at DESC LIMIT 1", [iid, market_close_utc(as_of)])
    if not got:
        return None
    from datetime import datetime, timezone
    return got[0][0], datetime.fromtimestamp(got[0][1], timezone.utc)


def thesis_tracker(ctx: AnalystContext) -> AnalystReport:
    aid = "thesis_tracker"
    book, h, out = _held(ctx, aid)
    if out:
        return out
    t = latest_thesis(ctx.lake, ctx.instrument_id, ctx.as_of)
    if t is None:
        return _report(ctx, aid, f"No thesis recorded for {h.symbol}. {LATER}",
                       gaps=["thesis: no thesis recorded (python -m smt.thesis set SYMBOL \"...\")"])
    text, when = t
    ev = [Evidence(id=f"theses:{ctx.instrument_id}:{when:%Y-%m-%d}", source_type="thesis",
                   ref=f"theses/{ctx.instrument_id}", as_of=when.date())]
    return _report(ctx, aid, f"Thesis for {h.symbol}, recorded {when:%Y-%m-%d}: \"{text}\" {LATER}", ev,
                   ["thesis: not checked against the facts (needs the LLM reviewers)"])
