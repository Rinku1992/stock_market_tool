"""Reverse valuation (equity research desk). Code only, no LLM. Views neutral, facts only.

Step 1: cost of capital, and ROIC against it. Inputs:
- owner-entered assumptions (smt.assumptions, the version entered on or before as_of);
- the 1-year beta to the Nifty 50 (same function the market_beta analyst stores);
- the lead ROIC view, its tax rate and its debt (valuation.lead_capital, same steps as capital_returns);
- market value of equity (valuation.market_value).
Maths: smt.analysts.cost_of_capital.

Step 2: what the current price assumes (never a forecast, a fair value or a verdict).
- NOPAT0 = 12-month EBIT x (1 - tax rate), lead view. Lead EV = market value + debt of the lead view on the
  latest balance sheet date + minority interest - cash, bank balances and current investments.
- No-growth share = (NOPAT0 / WACC) / lead EV.
- Implied growth: NOPAT grows at g for N years; reinvestment = NOPAT x g / R (R = return on new capital), so
  free cash flow = NOPAT x (1 - g / R). After year N new capital earns only the cost of capital, so the
  terminal value at N = NOPAT(N+1) / WACC. Discounted at WACC, end of year. g is solved (bisection, -10% to
  +60%) so that value = lead EV.
- Lenders: implied ROE = g + P/B x (cost of equity - g), from P/B = (ROE - g) / (cost of equity - g), for
  g = 0 and g = risk-free.
"""

from dataclasses import dataclass
from datetime import date

from smt import assumptions
from smt.analysts import cost_of_capital as cc
from smt.analysts.base import AnalystContext
from smt.analysts.market_activity import beta_1y
from smt.analysts.valuation import (CASH_LABEL, REAL_ESTATE, _base, _cash, _cr, _ebit, _month_end, _no_data, _report,
                                    lead_capital, market_value, roe_inputs)
from smt.router import LENDER
from smt.schema import AnalystReport

AID = "reverse_valuation"
G_MIN, G_MAX = -0.10, 0.60
YEARS, YEARS_LONG = 10, 15
NOT_FORECAST = "not a forecast and not an estimate of what the stock is worth"
NO_G_HIGH = "no growth rate up to 60% a year explains the price under these assumptions"
NO_G_LOW = "even a fall in profit of 10% a year gives a value above the price under these assumptions"
RF_GROWTH = "long-run growth assumed equal to the risk-free rate"
LATER = "Long history and peers come later."


# ---------------------------------------------------------------- pure maths (step 2)

def no_growth_value(nopat0: float, w: float) -> float:
    return nopat0 / w


def value_with_growth(nopat0: float, g: float, r: float, w: float, n: int) -> float:
    """Sum of discounted free cash flows for years 1..N + discounted terminal value NOPAT(N+1) / WACC."""
    v, nopat = 0.0, nopat0
    for t in range(1, n + 1):
        nopat *= 1 + g
        v += nopat * (1 - g / r) / (1 + w) ** t
    return v + nopat * (1 + g) / w / (1 + w) ** n


@dataclass(frozen=True)
class Solved:
    g: float | None
    why: str | None = None  # "r_le_w", "above" (no g up to 60%), "below" (value above EV even at -10%)


def implied_growth(ev: float, nopat0: float, r: float, w: float, n: int, rel_tol: float = 1e-10) -> Solved:
    """g in [-10%, +60%] such that value_with_growth = ev (value rises with g when r > w).
    Stops when the value is within rel_tol x EV of the EV."""
    if r <= w:
        return Solved(None, "r_le_w")
    f = lambda g: value_with_growth(nopat0, g, r, w, n) - ev
    lo, hi = G_MIN, G_MAX
    if f(hi) < 0:
        return Solved(None, "above")
    if f(lo) > 0:
        return Solved(None, "below")
    for _ in range(300):
        mid = (lo + hi) / 2
        fm = f(mid)
        if abs(fm) <= rel_tol * abs(ev):
            return Solved(mid)
        lo, hi = (mid, hi) if fm < 0 else (lo, mid)
    return Solved((lo + hi) / 2)


def implied_roe(pb: float, ke: float, g: float) -> float | None:
    """From P/B = (ROE - g) / (cost of equity - g). None if cost of equity <= g."""
    if ke <= g:
        return None
    return g + pb * (ke - g)


# ---------------------------------------------------------------- text helpers

def _p(x: float) -> str:
    return f"{x:.1%}"


def _p2(x: float) -> str:
    return f"{x:.2%}"


def _pts(x: float) -> str:
    return f"{x * 100:+.1f}"


def _range(lo: float, base: float, hi: float) -> str:
    return f"{_p(lo)} to {_p(hi)} (base {_p(base)})"


def _cell(s: Solved) -> str:
    return {None: "", "r_le_w": "no value from growth", "above": "none up to 60%",
            "below": "below -10%"}[s.why] or _p(s.g)


@dataclass
class Step1:
    """What step 2 needs from step 1."""
    ke: tuple[float, float, float] | None = None
    rf: float | None = None
    wacc: tuple[float, float, float] | None = None
    wacc_why: str | None = None
    lead: object = None
    mcap: object = None


# ---------------------------------------------------------------- analyst

def reverse_valuation(ctx: AnalystContext) -> AnalystReport:
    b = _base(ctx)
    if b is None:
        return _no_data(ctx, AID)
    gaps, feats = list(b.gaps), {}
    parts = [b.st.describe()]
    s1 = _step1(ctx, b, parts, gaps, feats)
    if s1 is not None:
        if b.st.label == LENDER:
            _step2_lender(ctx, b, s1, parts, gaps, feats)
        else:
            _step2(b, s1, parts, gaps, feats)
    return _report(ctx, AID, " ".join(parts), b, gaps, feats)


def _step1(ctx, b, parts, gaps, feats) -> Step1 | None:
    """Cost of capital and ROIC against it. None when there is no cost of equity."""
    inp, why = assumptions.inputs_asof(ctx.as_of, assumptions.FOLDER)
    if inp is None:
        parts.append(f"Cost of capital not computed: {why}.")
        gaps.append(why)
        return None
    for i in inp.values():
        if i.stale:
            gaps.append(f"cost of capital: {i.name} source date {i.source_date} is more than "
                        f"{assumptions.STALE_DAYS[i.name]} days before {ctx.as_of} (stale)")
    out, bwhy, bars = beta_1y(ctx.lake, ctx.instrument_id, ctx.as_of)
    if out is None:
        parts.append(f"Cost of capital not computed: no beta ({bwhy}).")
        gaps.append(f"beta: {bwhy}")
        return None
    beta = cc.blume_beta(out[0])

    g, d, erp, crp = (inp[k] for k in ("gsec_10y", "india_default_spread", "mature_erp", "india_crp"))
    rf = cc.risk_free(g.value, d.value)
    ke = cc.cost_of_equity_range(rf, beta, erp.value, crp.value)
    parts.append(
        f"Cost of equity {_range(ke[0], ke[1], ke[2])} = risk-free {_p2(rf)} + beta x (mature market equity risk premium "
        f"{_p2(erp.value)} + India country risk premium {_p2(crp.value)}). Risk-free {_p2(rf)} = {g.text()} minus "
        f"{d.text()}: {cc.RF_NOTE}. Inputs: {erp.text()}; {crp.text()}. Beta {beta.base:.2f} (range {beta.low:.2f} to "
        f"{beta.high:.2f}) = 0.67 x raw 1-year beta to Nifty 50 {beta.raw:.2f} (daily closes to {bars[-1].d}) + 0.33 "
        f"(Blume), kept inside {cc.BETA_MIN} to {cc.BETA_MAX}" + (" (limit applied)" if beta.clamped else "")
        + f"; {cc.BETA_LABEL}.")
    feats.update({"val_cost_of_equity": ke[1], "val_cost_of_equity_low": ke[0], "val_cost_of_equity_high": ke[2]})
    s1 = Step1(ke=ke, rf=rf)

    if b.st.label == LENDER:
        roe, end = roe_inputs(b)
        parts.append("No WACC for a lender: borrowed money is its raw material, not financing.")
        if roe is None:
            gaps.append("ROE not computed, so no spread")
        else:
            lo, hi = cc.spread_range(roe, ke[0], ke[2])
            parts.append(f"ROE {_p(roe)} (12 months to {end}) against cost of equity {_p(ke[0])} to {_p(ke[2])}: "
                         f"spread {_pts(lo)} to {_pts(hi)} points (base {_pts(roe - ke[1])}).")
            parts.append("Growth adds value only when ROE is above the cost of equity.")
            feats.update({"val_roe_spread": roe - ke[1], "val_roe_spread_low": lo, "val_roe_spread_high": hi})
        return s1

    lead, lwhy = lead_capital(ctx, b)
    if lead is None:
        gaps.append(f"WACC and spread: {lwhy}")
        s1.wacc_why = lwhy
        return s1
    s1.lead = lead
    fc, _ = b.fin.ttm(lead.end, "finance_costs")
    avg_debt = sum(lead.debt) / len(lead.debt)
    kd, kwhy = cc.cost_of_debt(fc, avg_debt)
    how = f"average of {lead.dates[1]} and {lead.end}" if len(lead.dates) == 2 else f"closing {lead.end} only"
    if kd is None:
        parts.append(f"Cost of debt not used: {kwhy} (12-month finance costs {_cr(fc)}, average debt {_cr(avg_debt)}, "
                     f"{how}; {lead.debt_text}). Cost of equity only: no WACC and no spread.")
        gaps.append(f"cost of debt: {kwhy}")
        s1.wacc_why = f"no WACC: {kwhy}"
        return s1
    note = ("; finance costs include lease interest but debt here has no leases (no verified lease figures)"
            if lead.view == "before leases" else "")
    parts.append(f"Cost of debt {_p(kd)} before tax = 12-month finance costs {_cr(fc)} to {lead.end} / average debt "
                 f"{_cr(avg_debt)} ({how}; debt {lead.view}: {lead.debt_text}){note}.")
    feats["val_cost_of_debt"] = kd
    mcap = market_value(ctx, b)
    if mcap.value is None:
        gaps.append(f"WACC: {mcap.why}")
        s1.wacc_why = mcap.why
        return s1
    s1.mcap = mcap
    gaps += mcap.gaps
    e, dbt = mcap.value, lead.debt[0]
    parts.append(f"Weights at market value: equity {_cr(e)} ({e / (e + dbt):.0%}; {mcap.text[0].lower() + mcap.text[1:]}), "
                 f"debt {_cr(dbt)} on {lead.end} ({dbt / (e + dbt):.0%}).")
    w = cc.wacc_range(e, dbt, ke, kd, lead.tax_rate)
    s1.wacc = w
    parts.append(f"WACC {_range(w[0], w[1], w[2])}, tax rate {lead.tax_rate:.0%} (the rate ROIC uses).")
    lo, hi = cc.spread_range(lead.roic, w[0], w[2])
    parts.append(f"ROIC {_p(lead.roic)} ({lead.view}) against cost of capital {_p(w[0])} to {_p(w[2])}: spread "
                 f"{_pts(lo)} to {_pts(hi)} points (base {_pts(lead.roic - w[1])}).")
    parts.append(cc.GROWTH_LINE)
    feats.update({"val_wacc": w[1], "val_wacc_low": w[0], "val_wacc_high": w[2], "val_roic_spread": lead.roic - w[1],
                  "val_roic_spread_low": lo, "val_roic_spread_high": hi})
    return s1


def lead_ev(b, lead, mcap_value: float) -> tuple[float | None, str, list[str]]:
    """Lead EV on the latest balance sheet date: market value + lead-view debt + minority interest - cash."""
    end = lead.end
    cash, cash_missing = _cash(b.fin, end)
    if cash is None:
        return None, "", [f"lead EV: cash not filed on {end}"]
    mi = b.fin.get(end, "I", "minority_interest")
    gaps = [f"lead EV: {m} not filed; left out of cash" for m in cash_missing]
    if mi is None:
        gaps.append("lead EV: minority interest not filed; left out")
    ev = mcap_value + lead.debt[0] + (mi or 0.0) - cash
    text = (f"enterprise value {lead.view} {_cr(ev)} (market value + debt {_cr(lead.debt[0])} + minority interest "
            f"{_cr(mi)} - {CASH_LABEL} {_cr(cash)} on {end})")
    return ev, text, gaps


def _yoy(b, end: date) -> str:
    """Latest year-on-year growth in 12-month revenue and EBIT, if 8 quarters are loaded."""
    prev = _month_end(end.year - 1, end.month)
    re = b.st.label == REAL_ESTATE
    rev, rev0 = b.fin.ttm(end, "revenue")[0], b.fin.ttm(prev, "revenue")[0]
    ebit, ebit0 = _ebit(b, end, [], with_other_income=re), _ebit(b, prev, [], with_other_income=re)
    if None in (rev, rev0, ebit, ebit0) or rev0 <= 0:
        n = len([q for q in b.fin.quarters() if q <= end])
        return (f"For comparison: year-on-year growth in 12-month revenue and EBIT: not loaded (needs 8 quarters to {end}; "
                f"{n} loaded). {LATER}")
    eb = f"EBIT {_p(ebit / ebit0 - 1)}" if ebit0 > 0 else f"EBIT not meaningful (12 months to {prev} was {_cr(ebit0)})"
    return (f"For comparison: 12-month revenue grew {_p(rev / rev0 - 1)} and {eb} year on year (12 months to {end} "
            f"against 12 months to {prev}). {LATER}")


def _step2(b, s1: Step1, parts, gaps, feats) -> None:
    """What the price assumes, for a company."""
    lead = s1.lead
    if s1.wacc is None:
        why = s1.wacc_why or "no WACC"
        parts.append(f"What the price assumes: not computed ({why}).")
        gaps.append(f"implied growth: not computed ({why})")
        return
    if lead.ebit <= 0:
        parts.append(f"What the price assumes: not computed (12-month EBIT is {_cr(lead.ebit)}, not positive).")
        gaps.append("implied growth: 12-month EBIT is not positive")
        return
    ev, ev_text, ev_gaps = lead_ev(b, lead, s1.mcap.value)
    gaps += ev_gaps
    if ev is None or ev <= 0:
        parts.append("What the price assumes: not computed (" + ("lead EV not available" if ev is None
                                                                 else f"lead EV is {_cr(ev)}, not positive") + ").")
        gaps.append("implied growth: no positive lead EV")
        return
    nopat0 = lead.ebit * (1 - lead.tax_rate)
    w_lo, w_base, w_hi = s1.wacc
    share = {w: no_growth_value(nopat0, w) / ev for w in s1.wacc}
    parts.append(f"What the price assumes ({NOT_FORECAST}): {ev_text}; NOPAT {_cr(nopat0)} (12-month EBIT "
                 f"{_cr(lead.ebit)} to {lead.end} x (1 - {lead.tax_rate:.0%})).")
    if b.st.label == REAL_ESTATE:
        parts.append("Real estate: profit is lumpy (booked on handover), so this result is not reliable.")
        gaps.append("implied growth: real estate profit is lumpy; result not reliable")
    s = (f"Today's profit with no growth explains {_p(share[w_base])} of the enterprise value "
         f"({_p(share[w_hi])} to {_p(share[w_lo])}).")
    s += (" The price assumes no growth, or a fall in profit." if share[w_base] >= 1
          else " The rest is growth the price already assumes.")
    parts.append(s)
    feats["val_no_growth_share"] = share[w_base]

    rows, notes = [], set()
    for view, r in lead.views.items():
        cells = [implied_growth(ev, nopat0, r, w, YEARS) for w in s1.wacc]
        notes |= {c.why for c in cells if c.why}
        rows.append(f"return on new capital = ROIC {view} {_p(r)}" + (" (lead)" if view == lead.view else "")
                    + ": " + " / ".join(_cell(c) for c in cells))
        if view == lead.view:
            for key, c in zip(("val_implied_growth_10y_low", "val_implied_growth_10y", "val_implied_growth_10y_high"),
                              cells):
                if c.g is not None:
                    feats[key] = c.g
    long = [implied_growth(ev, nopat0, lead.views[lead.view], w, YEARS_LONG) for w in s1.wacc]
    notes |= {c.why for c in long if c.why}
    if long[1].g is not None:
        feats["val_implied_growth_15y"] = long[1].g
    text = (f"Growth in NOPAT a year that the price assumes, for {YEARS} years (WACC {_p(w_lo)} / {_p(w_base)} / "
            f"{_p(w_hi)}): " + "; ".join(rows) + f". Lead case for {YEARS_LONG} years: "
            + " / ".join(_cell(c) for c in long) + ". Each year reinvestment = NOPAT x growth / return on new capital; "
            f"after the last year new capital earns only the cost of capital, so terminal value = next year's NOPAT / WACC.")
    if "r_le_w" in notes:
        text += (" No value from growth: return on new capital is not above WACC in that case, so growth adds no value "
                 "and no rate is solved.")
    if "above" in notes:
        text += f" None up to 60%: {NO_G_HIGH}."
    if "below" in notes:
        text += f" Below -10%: {NO_G_LOW}."
    parts.append(text)
    parts.append(_yoy(b, lead.end))


def _step2_lender(ctx, b, s1: Step1, parts, gaps, feats) -> None:
    """Implied lasting ROE from P/B."""
    roe, end = roe_inputs(b)
    mcap = market_value(ctx, b)
    eq = b.fin.get(end, "I", "equity_owners") if end else None
    if mcap.value is None or not eq:
        why = mcap.why or "owners' equity not filed"
        parts.append(f"What the price assumes: not computed ({why}).")
        gaps.append(f"implied ROE: {why}")
        return
    gaps += mcap.gaps
    pb = mcap.value / eq
    cases, vals = [], []
    for label, g in (("no growth", 0.0), (f"{RF_GROWTH} ({_p2(s1.rf)})", s1.rf)):
        r = [implied_roe(pb, k, g) for k in s1.ke]
        if None in r:
            cases.append(f"{label}: not computed (cost of equity is not above growth)")
            continue
        vals += r
        cases.append(f"{label}: {_p(r[0])} / {_p(r[1])} / {_p(r[2])}")
        if g == s1.rf:
            feats.update({"val_implied_roe": r[1], "val_implied_roe_low": r[0], "val_implied_roe_high": r[2]})
    parts.append(f"What the price assumes ({NOT_FORECAST}): P/B {pb:.2f}x ({mcap.text[0].lower() + mcap.text[1:]}; owners' "
                 f"equity {_cr(eq)} on {end}). Lasting ROE = growth + P/B x (cost of equity - growth), for cost of equity "
                 f"{_p(s1.ke[0])} / {_p(s1.ke[1])} / {_p(s1.ke[2])}: " + "; ".join(cases) + ".")
    if vals:
        last = f"The last 12 months' ROE was {_p(roe)}." if roe is not None else "The last 12 months' ROE is not computed."
        parts.append(f"The price assumes a lasting ROE of {_p(min(vals))} to {_p(max(vals))}. {last}")
    else:
        gaps.append("implied ROE: cost of equity is not above growth")
