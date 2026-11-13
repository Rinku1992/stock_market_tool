"""Stock type router (Router unit). Plain code, no LLM.

Labels a stock from the line items in its own filings, so each analyst can use the right playbook.

    lender  : any loaded filing has Loans (above zero), or has both InterestEarned (above zero)
              and ImpairmentOnFinancialInstruments. These tags come from the NBFC results
              taxonomy (checked on M&MFIN files, Nov 2026). None of the 33 general-company
              filings loaded so far (TCS, TITAN, GODREJPROP, NYKAA, FINCABLES, ASHOKLEY, TMPV) has them.
              Two rules because June and December filings carry no balance sheet, so no Loans.
    real_estate : not a lender, and either the index list labels its industry "Realty", or a filing
              names a reportable segment "Real Estate" or "Realty" (checked: GODREJPROP's filings do,
              no other loaded stock's do; GODREJPROP is not in the Nifty 100, DLF is the only "Realty"
              member there). Inventory share does not work: TITAN's jewellery stock is 71% of assets,
              the same as GODREJPROP's.
    general : filings are loaded and none of the above holds.
    unknown : no filings known as of the date. We do not guess.

The decision is per stock, not per file: one lender filing makes the stock a lender.
Bank filings are not checked yet (none loaded), so a bank filed under other tag names would
come out as "general". That is a known gap.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from smt.data.financials import load_financials, market_close_utc

LENDER, REAL_ESTATE, GENERAL, UNKNOWN = "lender", "real_estate", "general", "unknown"
_REALTY = re.compile(r"real\s*estate|realty", re.IGNORECASE)

# canonical line item -> the filing tag it comes from (see CANONICAL in smt.sources.nse_results)
_TAG = {"loans": "Loans", "interest_income": "InterestEarned", "credit_cost": "ImpairmentOnFinancialInstruments"}


@dataclass(frozen=True)
class StockType:
    label: str
    triggers: list[str] = field(default_factory=list)  # which tags made the call, so it can be checked

    def describe(self) -> str:
        name = self.label.replace("_", " ")
        if not self.triggers:
            return f"Stock type: {name}."
        return f"Stock type: {name} (router found {'; '.join(self.triggers)})."


def classify(rows: list[dict], segments: list[tuple[date, str]] = (), industry: str | None = None) -> StockType:
    """rows: financials rows for ONE stock, any basis (from load_financials).
    segments: (period_end, segment name) from its filings. industry: from the index list, if a member."""
    if not rows:
        return StockType(UNKNOWN)
    lender = _lender(rows)
    if lender.label == LENDER:
        return lender
    triggers = []
    if industry and _REALTY.search(industry):
        triggers.append(f"index list industry '{industry}'")
    named = sorted({(name, end) for end, name in segments if _REALTY.search(name)}, key=lambda t: t[1])
    if named:
        names = sorted({n for n, _ in named})
        triggers.append(f"segment {', '.join(repr(n) for n in names)} in {len({e for _, e in named})} filing(s), latest {named[-1][1]}")
    return StockType(REAL_ESTATE, triggers) if triggers else StockType(GENERAL)


def _lender(rows: list[dict]) -> StockType:
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


def _segments(lake, instrument_id: str | None, as_of: date) -> list[tuple[date, str]]:
    if lake is None or not instrument_id or not lake.has("filing_segments"):
        return []
    return lake.query(f"SELECT period_end, segment_name FROM {lake.read_sql('filing_segments')} "
                      "WHERE instrument_id = ? AND published_at <= ?", [instrument_id, market_close_utc(as_of)])


def _industry(lake, instrument_id: str | None) -> str | None:
    """Industry from the latest stored index list that has one. Not point-in-time (lists are today's)."""
    if lake is None or not instrument_id or not lake.has("index_members"):
        return None
    got = lake.query(f"SELECT industry FROM {lake.read_sql('index_members')} WHERE instrument_id = ? AND industry IS NOT NULL "
                     "ORDER BY valid_from DESC LIMIT 1", [instrument_id])
    return got[0][0] if got else None


def stock_type_for(lake, instrument_id: str | None, as_of: date) -> StockType:
    return classify(load_financials(lake, instrument_id, as_of), _segments(lake, instrument_id, as_of), _industry(lake, instrument_id))


def stock_type_router(ctx) -> str:
    """The graph's router step: AnalystContext -> label."""
    return stock_type_for(ctx.lake, ctx.instrument_id, ctx.as_of).label
