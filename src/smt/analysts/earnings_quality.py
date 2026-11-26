"""Earnings quality (equity research desk): the code part of "Forensic accounts". Code only, no LLM.

Facts and "Check:" lines only; every view is neutral. Limits come from src/smt/assumptions/earnings_quality.json
("starting values, judgement not measured"). Uses the financials loader (valuation.load_fin).

For the latest 12 months (and the 12 months a year earlier when loaded):
1. exceptional items / profit before tax; Check when above the limit either way;
2. other income / profit before tax; Check when above the limit;
3. effective tax rate = tax / profit before tax; Check when below the low or above the high limit; the standard
   company rate (about 25%) is printed as context, not verified;
4. non-lenders: receivable, inventory and payable days from balance sheets (average of opening and closing) on
   12-month revenue (inventory and payable days are on revenue, not cost of goods); against a year earlier when
   both years exist, else "one year only"; Check when receivables or inventories grew more than the limit (in
   points) faster than revenue. Jeweller rule: with any verified gold on loan row, payable days and the cash
   cycle are not computed (trade payables hold gold on loan in some filings).
Not repeated here: accruals, cash conversion, profit against revenue growth (other analysts print them).
Lenders: items 1 to 3 only. Real estate: all, with the note that revenue is lumpy. Missing input: "not computed"
with the reason, never zero.
"""

import json
from pathlib import Path

from smt.analysts import adjustments as adj
from smt.analysts.base import AnalystContext
from smt.analysts.valuation import _base, _month_end, _no_data, _report
from smt.router import LENDER, REAL_ESTATE
from smt.schema import AnalystReport

AID = "earnings_quality"
SETTINGS = Path(__file__).resolve().parents[1] / "assumptions" / "earnings_quality.json"
NOT_DONE = ("Not done: capitalised costs, depreciation changes, related-party deals, auditor remarks and revenue "
            "recognition; these need the annual report notes (later).")
GOLD_RULE = "not computed: trade payables hold gold on loan in some filings"


# ---------------------------------------------------------------- pure functions

def share(x: float | None, base: float | None) -> float | None:
    """x / base; None when either is missing or the base is not positive."""
    return None if x is None or base is None or base <= 0 else x / base


def days(balance: float | None, revenue: float | None) -> float | None:
    return None if balance is None or revenue is None or revenue <= 0 else balance / revenue * 365


def growth(now: float | None, before: float | None) -> float | None:
    return None if now is None or before is None or before <= 0 else now / before - 1


def faster(item_growth: float, revenue_growth: float, gap: float) -> bool:
    """True when an item grew more than `gap` (in points, as a fraction) faster than revenue."""
    return item_growth - revenue_growth > gap


def outside(x: float, low: float | None, high: float | None) -> bool:
    return (low is not None and x < low) or (high is not None and x > high)


def load_limits(path: Path | None = None) -> dict:
    return json.loads((path or SETTINGS).read_text())


# ---------------------------------------------------------------- analyst

def _pct(x):
    return f"{x:.1%}"


def earnings_quality(ctx: AnalystContext) -> AnalystReport:
    b = _base(ctx)
    if b is None:
        return _no_data(ctx, AID)
    st = load_limits()
    lim, label = st["limits"], st["label"]
    gaps, feats, parts, checks = list(b.gaps), {}, [b.st.describe()], []
    q = b.q
    prev_q = _month_end(q.year - 1, q.month)
    ttm = lambda end, item: b.fin.ttm(end, item)[0]  # noqa: E731
    pbt, pbt0 = ttm(q, "pbt"), ttm(prev_q, "pbt")
    parts.append(f"12 months to {q} ({b.fin.basis}); limits are {label}.")

    def ratio_line(name, item, key, check):
        now, before = share(ttm(q, item), pbt), share(ttm(prev_q, item), pbt0)
        if now is None:
            why = ("profit before tax is not positive" if pbt is not None and pbt <= 0 else
                   f"{name} or profit before tax not loaded for all 4 quarters")
            parts.append(f"{name[0].upper() + name[1:]} / profit before tax: not computed ({why}).")
            gaps.append(f"{name}: not computed ({why})")
            return
        feats[key] = now
        s = f"{name[0].upper() + name[1:]} {_pct(now)} of profit before tax"
        s += f" (a year earlier {_pct(before)})" if before is not None else " (a year earlier: not loaded)"
        parts.append(s + ".")
        msg = check(now)
        if msg:
            checks.append(msg)

    ratio_line("exceptional items", "exceptional_items", "eq_exceptional_share",
               lambda x: (f"Check: exceptional items are {_pct(x)} of profit before tax, beyond {_pct(lim['exceptional_share'])} "
                          "either way." if abs(x) > lim["exceptional_share"] else None))
    ratio_line("other income", "other_income", "eq_other_income_share",
               lambda x: (f"Check: other income is {_pct(x)} of profit before tax, above {_pct(lim['other_income_share'])}."
                          if x > lim["other_income_share"] else None))
    tax_now, tax0 = share(ttm(q, "tax"), pbt), share(ttm(prev_q, "tax"), pbt0)
    std = st["context"]["standard_tax_rate"]
    if tax_now is None:
        why = "profit before tax is not positive" if pbt is not None and pbt <= 0 else "tax or profit before tax not loaded"
        parts.append(f"Effective tax rate: not computed ({why}).")
        gaps.append(f"tax rate: not computed ({why})")
    else:
        feats["eq_tax_rate"] = tax_now
        parts.append(f"Effective tax rate {_pct(tax_now)} (tax / profit before tax)"
                     + (f", a year earlier {_pct(tax0)}" if tax0 is not None else ", a year earlier: not loaded")
                     + f"; standard company rate about {std:.0%} (context, not verified).")
        if outside(tax_now, lim["tax_rate_low"], lim["tax_rate_high"]):
            checks.append(f"Check: effective tax rate {_pct(tax_now)} is outside {_pct(lim['tax_rate_low'])} to "
                          f"{_pct(lim['tax_rate_high'])}.")

    if b.st.label != LENDER:
        _working_capital(ctx, b, lim, parts, gaps, feats, checks)
    if b.st.label == REAL_ESTATE:
        parts.append("Real estate: revenue is lumpy (booked on handover), so these ratios swing from year to year.")
    parts += checks
    parts.append(NOT_DONE)
    gaps.append("forensic items not done: capitalised costs, depreciation changes, related-party deals, auditor remarks, "
                "revenue recognition (need the annual report notes)")
    return _report(ctx, AID, " ".join(parts), b, gaps, feats)


def _working_capital(ctx, b, lim, parts, gaps, feats, checks) -> None:
    bs = b.fin.bs_dates()
    if not bs:
        parts.append("Working capital days: not computed (no balance sheet loaded).")
        gaps.append("working capital days: no balance sheet loaded")
        return
    end = bs[-1]
    op = b.fin.opening(end)
    op2 = b.fin.opening(op) if op else None
    end0 = _month_end(end.year - 1, end.month)
    rev, rev0 = b.fin.ttm(end, "revenue")[0], b.fin.ttm(end0, "revenue")[0]
    facts = adj.facts_asof(ctx.lake, ctx.instrument_id, ctx.as_of)
    gold = any(i == adj.GOLD and adj.usable(r) for (i, _), r in facts.items())

    def avg(item, d, o):
        c = b.fin.get(d, "I", item) if d else None
        p = b.fin.get(o, "I", item) if o else None
        return None if c is None else ((c + p) / 2 if p is not None else c)

    items = [("receivable", "trade_receivables", "eq_receivable_days", ""),
             ("inventory", "inventories", "eq_inventory_days", " (on revenue, not cost of goods)"),
             ("payable", "trade_payables", "eq_payable_days", " (on revenue, not cost of goods)")]
    two_years = op is not None and op2 is not None and rev0 is not None
    out, now, before = [], {}, {}
    for name, item, key, note in items:
        if name == "payable" and gold:
            out.append(f"payable days {GOLD_RULE}")
            gaps.append(f"payable days: {GOLD_RULE}")
            continue
        d = days(avg(item, end, op), rev)
        if d is None:
            why = "12-month revenue not loaded" if rev is None else f"{item.replace('_', ' ')} not filed on {end}"
            out.append(f"{name} days not computed ({why})")
            gaps.append(f"{name} days: not computed ({why})")
            continue
        now[name], feats[key] = d, d
        s = f"{name} days {d:.0f}{note}"
        d0 = days(avg(item, op, op2), rev0) if two_years else None
        if d0 is not None:
            before[name] = d0
            s += f", a year earlier {d0:.0f}"
        out.append(s)
    how = "average of opening and closing balances" if op else "closing balances only (no balance sheet a year earlier)"
    line = f"Working capital on 12-month revenue to {end} ({how}): " + "; ".join(out)
    if gold:
        line += "; cash cycle " + GOLD_RULE
    elif {"receivable", "inventory", "payable"} <= set(now):
        line += f"; cash cycle {now['receivable'] + now['inventory'] - now['payable']:.0f} days"
    parts.append(line + "." + ("" if before else " Against a year earlier: one year only."))
    rg = growth(rev, rev0)
    for name, item in (("receivables", "trade_receivables"), ("inventories", "inventories")):
        g = growth(b.fin.get(end, "I", item), b.fin.get(op, "I", item) if op else None)
        if g is None or rg is None:
            gaps.append(f"{name} growth against revenue: not computed (needs the balance sheet and 12-month revenue a "
                        "year earlier)")
            continue
        if faster(g, rg, lim["working_capital_gap"]):
            checks.append(f"Check: {name} grew {_pct(g)} while 12-month revenue grew {_pct(rg)}, more than "
                          f"{lim['working_capital_gap'] * 100:.0f} points faster.")
