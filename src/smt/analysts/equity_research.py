"""Equity research desk. Code only for now.

financial_trend reports what the company's own filings say: growth, margins, debt and cash
conversion. It gives no direction yet: there is not enough filed history loaded to test any
rule, so every view is neutral and the report says why.
"""

from datetime import date, timedelta

from smt.analysts.base import AnalystContext
from smt.data.financials import load_financials
from smt.schema import AnalystKind, AnalystReport, Evidence, neutral_views

UNIT = "equity_research"
CRORE = 1e7


def _crore(v: float) -> str:
    return f"Rs {v / CRORE:,.0f} crore"


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v:+.1%}" if v < 0 or v > 0 else "0.0%"


def _report(ctx, finding, evidence=None, gaps=None) -> AnalystReport:
    return AnalystReport(analyst_id="financial_trend", unit=UNIT, kind=AnalystKind.CODE, symbol=ctx.symbol,
                         as_of=ctx.as_of, finding=finding, views=neutral_views(), evidence=evidence or [],
                         could_not_check=gaps or [])


def _pick(rows: list[dict], period_end: date, period_type: str, item: str) -> float | None:
    for r in rows:
        if r["period_end"] == period_end and r["period_type"] == period_type and r["line_item"] == item:
            return r["value"]
    return None


def _same_quarter_last_year(rows: list[dict], end: date) -> date | None:
    for r in rows:
        if r["period_type"] == "Q" and r["line_item"] == "revenue" and abs((r["period_end"] - (end - timedelta(days=365))).days) <= 7:
            return r["period_end"]
    return None


def financial_trend(ctx: AnalystContext) -> AnalystReport:
    rows = load_financials(ctx.lake, ctx.instrument_id, ctx.as_of, consolidated=True)
    basis = "consolidated"
    if not any(r["line_item"] == "revenue" for r in rows):
        rows, basis = load_financials(ctx.lake, ctx.instrument_id, ctx.as_of, consolidated=False), "standalone"
    quarters = sorted({r["period_end"] for r in rows if r["period_type"] == "Q" and r["line_item"] == "revenue"})
    if not quarters:
        return _report(ctx, "No quarterly results known for this stock as of this date.", gaps=["financials: no results known as of this date"])

    q = quarters[-1]
    gaps = ["direction rule: not enough filed history loaded to test one"]
    rev, np_ = _pick(rows, q, "Q", "revenue"), _pick(rows, q, "Q", "net_profit")
    pbt, fin, dep, oth = (_pick(rows, q, "Q", k) for k in ("pbt", "finance_costs", "depreciation", "other_income"))
    parts = [f"Latest quarter ended {q} ({basis}): revenue {_crore(rev)}, net profit {_crore(np_)}, "
             f"net margin {np_ / rev:.1%}."]
    features = {"fin_revenue_q": rev, "fin_net_profit_q": np_, "fin_net_margin_q": np_ / rev}
    if None not in (pbt, fin, dep, oth):
        ebitda = pbt + fin + dep - oth
        parts.append(f"Operating profit before depreciation (EBITDA) {_crore(ebitda)}, margin {ebitda / rev:.1%}.")
        features["fin_ebitda_margin_q"] = ebitda / rev

    prev = _same_quarter_last_year(rows, q)
    if prev:
        g_rev = rev / _pick(rows, prev, "Q", "revenue") - 1
        prev_np = _pick(rows, prev, "Q", "net_profit")
        g_np = np_ / prev_np - 1 if prev_np and prev_np > 0 else None
        parts.append(f"Versus the same quarter last year: revenue {_pct(g_rev)}, net profit {_pct(g_np)}.")
        features["fin_rev_growth_yoy"] = g_rev
        if g_np is not None:
            features["fin_np_growth_yoy"] = g_np
        # one-offs: show exceptional items and growth before them, so a big jump can be judged
        ex_now, ex_prev = _pick(rows, q, "Q", "exceptional_items") or 0.0, _pick(rows, prev, "Q", "exceptional_items") or 0.0
        if ex_now or ex_prev:
            parts.append(f"Exceptional items: {_crore(ex_now)} this quarter, {_crore(ex_prev)} a year ago.")
        core_now, core_prev = _pick(rows, q, "Q", "pbt_before_exceptional"), _pick(rows, prev, "Q", "pbt_before_exceptional")
        if core_now is not None and core_prev and core_prev > 0:
            g_core = core_now / core_prev - 1
            parts.append(f"Profit before tax and exceptional items {_pct(g_core)}.")
            features["fin_core_pbt_growth_yoy"] = g_core
        if g_np is not None and abs(g_np) >= 0.5 and abs(g_np) > 2 * abs(g_rev):
            parts.append("Check: profit moved far more than revenue, so look for one-offs or a weak base quarter.")
    else:
        gaps.append("growth: same quarter last year not loaded")

    bs_dates = sorted({r["period_end"] for r in rows if r["period_type"] == "I" and r["line_item"] == "equity"})
    if bs_dates:
        b = bs_dates[-1]
        eq = _pick(rows, b, "I", "equity")
        debt = sum(_pick(rows, b, "I", k) or 0.0 for k in ("borrowings_noncurrent", "borrowings_current"))
        cash = sum(_pick(rows, b, "I", k) or 0.0 for k in ("cash", "current_investments"))
        parts.append(f"Balance sheet on {b}: borrowings {_crore(debt)}, cash and current investments {_crore(cash)}, "
                     f"net debt {_crore(debt - cash)}, debt to equity {debt / eq:.2f}.")
        features.update({"fin_debt_to_equity": debt / eq, "fin_net_debt": debt - cash})
    else:
        gaps.append("balance sheet: not in the loaded filings")

    cf = [r for r in rows if r["line_item"] == "cfo"]
    if cf:
        c = max(cf, key=lambda r: (r["period_end"], r["period_type"]))
        np_cum = _pick(rows, c["period_end"], c["period_type"], "net_profit")
        capex = _pick(rows, c["period_end"], c["period_type"], "capex")
        label = {"Q": "quarter", "H": "half year", "N": "nine months", "A": "year"}[c["period_type"]]
        line = f"Cash flow, {label} to {c['period_end']}: operating cash {_crore(c['value'])}"
        if np_cum:
            line += f", {c['value'] / np_cum:.2f} times net profit"
            features["fin_cash_conversion"] = c["value"] / np_cum
        if capex is not None:
            line += f"; capex {_crore(capex)}, free cash flow {_crore(c['value'] - capex)}"
        parts.append(line + ".")
        if c["value"] < 0:
            parts.append("Check: operating cash flow is negative while the company reports a profit.")
    else:
        gaps.append("cash flow: not in the loaded filings")

    for name, value in features.items():
        ctx.store.put_feature(symbol=ctx.instrument_id, name=name, value=float(value), known_at=ctx.as_of,
                              origin="code", source="financial_trend")
    known = max(r["published_at"] for r in rows).date()
    ev = [Evidence(id=f"financials:{ctx.instrument_id}:{q}", source_type="filing",
                   ref=f"financials/{ctx.instrument_id}/{q}", as_of=min(known, ctx.as_of))]
    return _report(ctx, " ".join(parts), ev, gaps)
