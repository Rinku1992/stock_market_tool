"""Stock type router (Router unit). Plain code, no LLM.

Labels a stock from the line items in its own filings, so each analyst can use the right playbook.

    lender  : any loaded filing has Loans (above zero), or has both InterestEarned (above zero)
              and ImpairmentOnFinancialInstruments. These tags come from the NBFC results
              taxonomy (checked on M&MFIN files, Nov 2026). None of the 33 general-company
              filings loaded so far (TCS, TITAN, GODREJPROP, NYKAA, FINCABLES, ASHOKLEY, TMPV) has them.
              Two rules because June and December filings carry no balance sheet, so no Loans.
    general : filings are loaded and none of them looks like a lender.
    unknown : no filings known as of the date. We do not guess.

The decision is per stock, not per file: one lender filing makes the stock a lender.
Bank filings are not checked yet (none loaded), so a bank filed under other tag names would
come out as "general". That is a known gap.
"""

from dataclasses import dataclass, field
from datetime import date

from smt.data.financials import load_financials

LENDER, GENERAL, UNKNOWN = "lender", "general", "unknown"

# canonical line item -> the filing tag it comes from (see CANONICAL in smt.sources.nse_results)
_TAG = {"loans": "Loans", "interest_income": "InterestEarned", "credit_cost": "ImpairmentOnFinancialInstruments"}


@dataclass(frozen=True)
class StockType:
    label: str
    triggers: list[str] = field(default_factory=list)  # which tags made the call, so it can be checked

    def describe(self) -> str:
        if self.label != LENDER:
            return f"Stock type: {self.label}."
        return f"Stock type: lender (router found {'; '.join(self.triggers)})."


def classify(rows: list[dict]) -> StockType:
    """rows: financials rows for ONE stock, any basis (from load_financials)."""
    if not rows:
        return StockType(UNKNOWN)
    by_period: dict[date, dict[str, float]] = {}
    for r in rows:
        if r["line_item"] in _TAG:
            by_period.setdefault(r["period_end"], {})[r["line_item"]] = r["value"]
    hits: dict[str, list[date]] = {}
    for end, items in by_period.items():
        if items.get("loans", 0) > 0:
            hits.setdefault("loans", []).append(end)
        if items.get("interest_income", 0) > 0 and "credit_cost" in items:
            hits.setdefault("interest_income+credit_cost", []).append(end)
    if not hits:
        return StockType(GENERAL)
    triggers = []
    for key in sorted(hits):
        tags = " and ".join(_TAG[k] for k in key.split("+"))
        ends = sorted(hits[key])
        triggers.append(f"{tags} in {len(ends)} period(s), latest {ends[-1]}")
    return StockType(LENDER, triggers)


def stock_type_for(lake, instrument_id: str | None, as_of: date) -> StockType:
    return classify(load_financials(lake, instrument_id, as_of))


def stock_type_router(ctx) -> str:
    """The graph's router step: AnalystContext -> label."""
    return stock_type_for(ctx.lake, ctx.instrument_id, ctx.as_of).label
