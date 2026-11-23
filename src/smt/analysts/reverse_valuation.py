"""Reverse valuation (equity research desk), step 1: cost of capital, and ROIC against it.

Code only, no LLM. Views neutral, facts only. Inputs:
- owner-entered assumptions (smt.assumptions, the version entered on or before as_of);
- the 1-year beta to the Nifty 50 (same function the market_beta analyst stores);
- the lead ROIC view, its tax rate and its debt (valuation.lead_capital, same steps as capital_returns);
- market value of equity (valuation.market_value).
Maths: smt.analysts.cost_of_capital.
"""

from smt import assumptions
from smt.analysts import cost_of_capital as cc
from smt.analysts.base import AnalystContext
from smt.analysts.market_activity import beta_1y
from smt.analysts.valuation import _base, _cr, _no_data, _report, lead_capital, market_value, roe_inputs
from smt.router import LENDER
from smt.schema import AnalystReport

AID = "reverse_valuation"


def _p(x: float) -> str:
    return f"{x:.1%}"


def _pts(x: float) -> str:
    return f"{x * 100:+.1f}"


def _range(lo: float, base: float, hi: float) -> str:
    return f"{_p(lo)} to {_p(hi)} (base {_p(base)})"


def reverse_valuation(ctx: AnalystContext) -> AnalystReport:
    b = _base(ctx)
    if b is None:
        return _no_data(ctx, AID)
    gaps, feats = list(b.gaps), {}
    parts = [b.st.describe()]

    inp, why = assumptions.inputs_asof(ctx.as_of, assumptions.FOLDER)
    if inp is None:
        return _report(ctx, AID, " ".join(parts + [f"Cost of capital not computed: {why}."]), b, gaps + [why])
    for i in inp.values():
        if i.stale:
            gaps.append(f"cost of capital: {i.name} source date {i.source_date} is more than "
                        f"{assumptions.STALE_DAYS[i.name]} days before {ctx.as_of} (stale)")
    out, bwhy, bars = beta_1y(ctx.lake, ctx.instrument_id, ctx.as_of)
    if out is None:
        return _report(ctx, AID, " ".join(parts + [f"Cost of capital not computed: no beta ({bwhy})."]), b,
                       gaps + [f"beta: {bwhy}"])
    beta = cc.blume_beta(out[0])

    g, d, erp, crp = (inp[k] for k in ("gsec_10y", "india_default_spread", "mature_erp", "india_crp"))
    rf = cc.risk_free(g.value, d.value)
    ke = cc.cost_of_equity_range(rf, beta, erp.value, crp.value)
    parts.append(
        f"Cost of equity {_range(ke[0], ke[1], ke[2])} = risk-free {_p(rf)} + beta x (mature market equity risk premium "
        f"{_p(erp.value)} + India country risk premium {_p(crp.value)}). Risk-free {_p(rf)} = {g.text()} minus "
        f"{d.text()}: {cc.RF_NOTE}. Inputs: {erp.text()}; {crp.text()}. Beta {beta.base:.2f} (range {beta.low:.2f} to "
        f"{beta.high:.2f}) = 0.67 x raw 1-year beta to Nifty 50 {beta.raw:.2f} (daily closes to {bars[-1].d}) + 0.33 "
        f"(Blume), kept inside {cc.BETA_MIN} to {cc.BETA_MAX}" + (" (limit applied)" if beta.clamped else "")
        + f"; {cc.BETA_LABEL}.")
    feats["val_cost_of_equity"] = ke[1]

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
            feats["val_roe_spread"] = roe - ke[1]
        return _report(ctx, AID, " ".join(parts), b, gaps, feats)

    lead, lwhy = lead_capital(ctx, b)
    if lead is None:
        gaps.append(f"WACC and spread: {lwhy}")
        return _report(ctx, AID, " ".join(parts), b, gaps, feats)
    fc, missing = b.fin.ttm(lead.end, "finance_costs")
    avg_debt = sum(lead.debt) / len(lead.debt)
    kd, kwhy = cc.cost_of_debt(fc, avg_debt)
    how = f"average of {lead.dates[1]} and {lead.end}" if len(lead.dates) == 2 else f"closing {lead.end} only"
    if kd is None:
        parts.append(f"Cost of debt not used: {kwhy} (12-month finance costs {_cr(fc)}, average debt {_cr(avg_debt)}, "
                     f"{how}; {lead.debt_text}). Cost of equity only: no WACC and no spread.")
        gaps.append(f"cost of debt: {kwhy}")
        return _report(ctx, AID, " ".join(parts), b, gaps, feats)
    note = ("; finance costs include lease interest but debt here has no leases (no verified lease figures)"
            if lead.view == "before leases" else "")
    parts.append(f"Cost of debt {_p(kd)} before tax = 12-month finance costs {_cr(fc)} to {lead.end} / average debt "
                 f"{_cr(avg_debt)} ({how}; debt {lead.view}: {lead.debt_text}){note}.")
    mcap = market_value(ctx, b)
    if mcap.value is None:
        gaps.append(f"WACC: {mcap.why}")
        return _report(ctx, AID, " ".join(parts), b, gaps, feats | {"val_cost_of_debt": kd})
    gaps += mcap.gaps
    e, dbt = mcap.value, lead.debt[0]
    parts.append(f"Weights at market value: equity {_cr(e)} ({e / (e + dbt):.0%}; {mcap.text[0].lower() + mcap.text[1:]}), debt {_cr(dbt)} on "
                 f"{lead.end} ({dbt / (e + dbt):.0%}).")
    w = cc.wacc_range(e, dbt, ke, kd, lead.tax_rate)
    parts.append(f"WACC {_range(w[0], w[1], w[2])}, tax rate {lead.tax_rate:.0%} (the rate ROIC uses).")
    lo, hi = cc.spread_range(lead.roic, w[0], w[2])
    parts.append(f"ROIC {_p(lead.roic)} ({lead.view}) against cost of capital {_p(w[0])} to {_p(w[2])}: spread "
                 f"{_pts(lo)} to {_pts(hi)} points (base {_pts(lead.roic - w[1])}).")
    parts.append(cc.GROWTH_LINE)
    feats.update({"val_cost_of_debt": kd, "val_wacc": w[1], "val_wacc_low": w[0], "val_wacc_high": w[2],
                  "val_roic_spread": lead.roic - w[1]})
    return _report(ctx, AID, " ".join(parts), b, gaps, feats)
