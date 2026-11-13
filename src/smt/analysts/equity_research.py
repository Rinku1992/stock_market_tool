"""Equity research desk. Code only for now.

financial_trend reports what the company's own filings say: growth, margins, debt and cash
conversion. Lenders (banks, NBFCs; see smt.router) get their own playbook: loans, borrowings
under their real names, net interest income, credit cost and debt to net worth. EBITDA and the
negative operating cash check mean nothing for a lender and are skipped. It gives no direction yet: there is not enough filed history loaded to test any
rule, so every view is neutral and the report says why.
"""

from datetime import date, timedelta

from smt.analysts.base import AnalystContext
from smt.data.financials import load_financials
from smt.router import LENDER, StockType, stock_type_for
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


def _same_quarter_last_year(rows: list[dict], end: date, item: str = "revenue", period_type: str = "Q") -> date | None:
    for r in rows:
        if r["period_type"] == period_type and r["line_item"] == item and abs((r["period_end"] - (end - timedelta(days=365))).days) <= 7:
            return r["period_end"]
    return None


def _growth(now: float | None, prev: float | None) -> float | None:
    """Growth only when both values are filed and the base is positive. A missing value is never zero."""
    return None if now is None or prev is None or prev <= 0 else now / prev - 1


def financial_trend(ctx: AnalystContext) -> AnalystReport:
    rows = load_financials(ctx.lake, ctx.instrument_id, ctx.as_of, consolidated=True)
    basis = "consolidated"
    if not any(r["line_item"] == "revenue" for r in rows):
        rows, basis = load_financials(ctx.lake, ctx.instrument_id, ctx.as_of, consolidated=False), "standalone"
    quarters = sorted({r["period_end"] for r in rows if r["period_type"] == "Q" and r["line_item"] == "revenue"})
    if not quarters:
        return _report(ctx, "No quarterly results known for this stock as of this date.", gaps=["financials: no results known as of this date"])

    st = stock_type_for(ctx.lake, ctx.instrument_id, ctx.as_of)  # per stock: all filings, both bases
    if st.label == LENDER:
        return _lender_trend(ctx, rows, basis, quarters[-1], st)
    return _general_trend(ctx, rows, basis, quarters[-1])


def _general_trend(ctx: AnalystContext, rows: list[dict], basis: str, q: date) -> AnalystReport:
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
        found = [v for k in ("borrowings_noncurrent", "borrowings_current") if (v := _pick(rows, b, "I", k)) is not None]
        cash = sum(_pick(rows, b, "I", k) or 0.0 for k in ("cash", "current_investments"))
        if found:
            debt = sum(found)
            parts.append(f"Balance sheet on {b}: borrowings {_crore(debt)}, cash and current investments {_crore(cash)}, "
                         f"net debt {_crore(debt - cash)}, debt to equity {debt / eq:.2f}.")
            features.update({"fin_debt_to_equity": debt / eq, "fin_net_debt": debt - cash})
        else:
            # a missing tag is not zero debt: lenders and banks file borrowings under other names
            parts.append(f"Balance sheet on {b}: borrowings not found under the usual names, so debt is not reported.")
            gaps.append("debt: borrowings not found in the filing (financial companies use other line names)")
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


LENDER_DEBT = [  # (canonical item, the filing's own name)
    ("borrowings_other_than_debt_securities", "Borrowings (other than debt securities)"),
    ("debt_securities", "Debt securities"),
    ("deposits", "Deposits"),
    ("subordinated_liabilities", "Subordinated liabilities"),
]


def _store_and_report(ctx, rows, q, parts, features, gaps) -> AnalystReport:
    for name, value in features.items():
        ctx.store.put_feature(symbol=ctx.instrument_id, name=name, value=float(value), known_at=ctx.as_of,
                              origin="code", source="financial_trend")
    known = max(r["published_at"] for r in rows).date()
    ev = [Evidence(id=f"financials:{ctx.instrument_id}:{q}", source_type="filing",
                   ref=f"financials/{ctx.instrument_id}/{q}", as_of=min(known, ctx.as_of))]
    return _report(ctx, " ".join(parts), ev, gaps)


def _lender_trend(ctx: AnalystContext, rows: list[dict], basis: str, q: date, st: StockType) -> AnalystReport:
    gaps = ["direction rule: not enough filed history loaded to test one"]
    parts = [st.describe()]
    features: dict[str, float] = {}
    pick = lambda end, item, pt="Q": _pick(rows, end, pt, item)  # noqa: E731
    inc, np_, ii, fc, cc = (pick(q, k) for k in ("total_income", "net_profit", "interest_income", "finance_costs", "credit_cost"))

    line = f"Latest quarter ended {q} ({basis}):"
    line += f" total income {_crore(inc)}," if inc is not None else " total income not found,"
    line += f" net profit {_crore(np_)}" if np_ is not None else " net profit not found"
    if inc and np_ is not None:
        line += f", net margin {np_ / inc:.1%} (computed by us: net profit / total income)"
        features["fin_net_margin_q"] = np_ / inc
    parts.append(line + ".")
    if np_ is not None:
        features["fin_net_profit_q"] = np_

    nii = ii - fc if ii is not None and fc is not None else None
    if nii is not None:
        parts.append(f"Interest earned {_crore(ii)}, finance costs {_crore(fc)}, "
                     f"net interest income {_crore(nii)} (computed by us: interest earned minus finance costs).")
        features["fin_nii_q"] = nii
    else:
        missing = [n for n, v in (("interest earned", ii), ("finance costs", fc)) if v is None]
        parts.append(f"Net interest income not computed: {' and '.join(missing)} not found.")
        gaps.append("net interest income: " + " and ".join(missing) + " not in the filing")

    # balance sheet: the latest one on or before the quarter (June and December filings carry none)
    bs_dates = sorted({r["period_end"] for r in rows if r["period_type"] == "I" and r["line_item"] in ("equity", "loans", "total_assets")
                       and r["period_end"] <= q})
    loans = b = None
    if bs_dates:
        b = bs_dates[-1]
        loans, assets, eq = pick(b, "loans", "I"), pick(b, "total_assets", "I"), pick(b, "equity", "I")
        if loans is not None:
            line = f"Balance sheet on {b}: loans {_crore(loans)}"
            features["fin_loans"] = loans
        elif assets is not None:
            line = f"Balance sheet on {b}: loans not tagged, total assets {_crore(assets)}"
            gaps.append("loans: not tagged in the filing, total assets shown instead")
        else:
            line = f"Balance sheet on {b}: loans and total assets not found"
            gaps.append("loans: neither loans nor total assets in the filing")
        found = [(name, v) for item, name in LENDER_DEBT if (v := pick(b, item, "I")) is not None]
        missing = [name for item, name in LENDER_DEBT if pick(b, item, "I") is None]
        if found:
            line += "; " + ", ".join(f"{name.lower()} {_crore(v)}" for name, v in found)
        if missing:
            line += f"; not found: {', '.join(m.lower() for m in missing)}, so total borrowings and debt to net worth are not reported"
            gaps.append("borrowings: " + ", ".join(m.lower() for m in missing) + " not in the filing")
        else:
            debt = sum(v for _, v in found)
            line += f"; total borrowings {_crore(debt)}"
            features["fin_lender_borrowings"] = debt
            if eq:
                line += f", net worth (total equity) {_crore(eq)}, debt to net worth {debt / eq:.2f} (computed by us)"
                features["fin_debt_to_equity"] = debt / eq
            else:
                gaps.append("net worth: total equity not in the filing")
        parts.append(line + ".")
    else:
        gaps.append("balance sheet: not in the loaded filings")

    if cc is not None:
        line = f"Credit cost (impairment on financial instruments) {_crore(cc)} this quarter"
        features["fin_credit_cost_q"] = cc
        if loans:
            ratio = cc * 4 / loans
            line += f", {ratio:.2%} of loans a year (computed by us: quarter x 4 / loans on {b})"
            features["fin_credit_cost_to_loans_ann"] = ratio
        else:
            gaps.append("credit cost to loans: loans not found")
        parts.append(line + ".")
    else:
        gaps.append("credit cost: impairment on financial instruments not in the filing")

    prev = _same_quarter_last_year(rows, q, "net_profit")
    if prev:
        g = {"total income": _growth(inc, pick(prev, "total_income")),
             "net profit": _growth(np_, pick(prev, "net_profit"))}
        pii, pfc = pick(prev, "interest_income"), pick(prev, "finance_costs")
        g["net interest income"] = _growth(nii, pii - pfc if pii is not None and pfc is not None else None)
        g["credit cost"] = _growth(cc, pick(prev, "credit_cost"))
        parts.append("Versus the same quarter last year: " + ", ".join(f"{k} {_pct(v)}" for k, v in g.items()) + ".")
        for k, name in (("total income", "fin_income_growth_yoy"), ("net profit", "fin_np_growth_yoy"), ("net interest income", "fin_nii_growth_yoy")):
            if g[k] is not None:
                features[name] = g[k]
    else:
        gaps.append("growth: same quarter last year not loaded")

    if b is not None:
        pb = _same_quarter_last_year(rows, b, "loans", "I") or _same_quarter_last_year(rows, b, "total_assets", "I")
        if pb:
            g_loans = _growth(loans, pick(pb, "loans", "I"))
            prev_debt = [pick(pb, item, "I") for item, _ in LENDER_DEBT]
            g_debt = _growth(features.get("fin_lender_borrowings"), None if None in prev_debt else sum(prev_debt))
            parts.append(f"Versus the balance sheet on {pb}: loans {_pct(g_loans)}, total borrowings {_pct(g_debt)}.")
            if g_loans is not None:
                features["fin_loan_growth_yoy"] = g_loans
        else:
            gaps.append("loan growth: balance sheet a year earlier not loaded")

    parts.append("EBITDA and operating cash flow are not used for lenders (giving out loans counts as operating cash outflow).")
    return _store_and_report(ctx, rows, q, parts, features, gaps)
