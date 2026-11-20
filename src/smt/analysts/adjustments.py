"""Accounting adjustments (valuation stage B, step 2): leases and R&D from verified annual report facts.

Pure functions. valuation.py calls them. Facts come only from report_facts through
owner_review.current_facts, as known at the as_of market close (same rule as load_financials), and only
rows with verified = true and a value are used. Missing is never zero: a missing figure means no
adjusted number, and a reason.

Leases (Ind AS 116):
- lease total for a fiscal year end = current + non-current lease liabilities, both verified.
- lease figure for a balance sheet date d = the total for the latest fiscal year end on or before d,
  if that year end is no more than 400 days before d. If the latest year end with any lease row lacks
  a verified total, there is no figure (no silent fall back to an older year).
- EBIT and EBITDA are NOT adjusted: lease interest is inside finance costs, which EBIT already adds
  back, and right-of-use depreciation is inside depreciation. Checked per stock: lease interest for
  the fiscal year must not exceed that year's 4 quarters of finance costs.

R&D (Damodaran, rough): 3-year life, straight line. R0 = latest fiscal year, R1, R2, R3 earlier.
Years before the earliest known year are assumed equal to it.
    research asset = R0 + 2/3 R1 + 1/3 R2;  amortisation = (R1 + R2 + R3) / 3
    adjusted EBIT = EBIT + R0 - amortisation;  adjusted NOPAT = EBIT (1 - t) + (R0 - amortisation)
"""

from dataclasses import dataclass
from datetime import date

from smt.data.financials import market_close_utc

MAX_AGE_DAYS = 400
LEASE_CURRENT, LEASE_NONCURRENT = "lease_liabilities_current", "lease_liabilities_noncurrent"
LEASE_INTEREST = "interest_on_lease_liabilities"
RD_NARROW, RD_WIDE = "rnd_expenditure", "rd_and_innovation_total"
RD_LIFE = 3
GOLD = "gold_on_loan"
RD_MATERIAL = 0.01  # R&D below 1% of revenue: not material
GOLD_NOTE = ("gold on loan is treated as debt here: it funds inventory and its interest is in finance costs. Some "
             "analysts treat it as working capital; that view is the 'including leases' figure")


def facts_asof(lake, instrument_id: str, as_of: date) -> dict[tuple[str, date], dict]:
    """Current report_facts rows (verified or not) as known at the as_of close, keyed by (item, fiscal year end)."""
    if lake is None or not instrument_id or not lake.has("report_facts"):
        return {}
    from smt.sources.owner_review import current_facts

    return {(r["item"], r["fiscal_year_end"]): r for r in current_facts(lake, instrument_id, market_close_utc(as_of))}


def usable(row: dict | None) -> bool:
    return bool(row) and row["verified"] is True and row["value"] is not None


def _fy(d: date) -> str:
    return f"FY{d.year % 100:02d}"


# ---------------------------------------------------------------- leases

def lease_totals(facts: dict) -> dict[date, float | None]:
    """Fiscal year end -> current + non-current lease liabilities; None where a lease row exists but the
    two are not both verified."""
    years = {fy for (item, fy) in facts if item in (LEASE_CURRENT, LEASE_NONCURRENT)}
    out = {}
    for fy in years:
        cur, non = facts.get((LEASE_CURRENT, fy)), facts.get((LEASE_NONCURRENT, fy))
        out[fy] = cur["value"] + non["value"] if usable(cur) and usable(non) else None
    return out


@dataclass
class Lease:
    value: float | None
    fiscal_year_end: date | None
    why: str | None = None  # set when value is None


def lease_on(totals: dict[date, float | None], d: date) -> Lease:
    years = [fy for fy in totals if fy <= d]
    if not years:
        return Lease(None, None, f"no lease liabilities from an annual report for a fiscal year end on or before {d}")
    fy = max(years)
    if totals[fy] is None:
        return Lease(None, fy, f"lease liabilities for {fy}: current and non-current are not both verified")
    if (d - fy).days > MAX_AGE_DAYS:
        return Lease(None, fy, f"latest lease liabilities are for {fy}, more than {MAX_AGE_DAYS} days before {d}")
    return Lease(totals[fy], fy)


def lease_interest_check(facts: dict, fy: date, finance_costs_fy: float | None) -> str | None:
    """None if lease interest for fiscal year fy is within that year's finance costs; else the reason."""
    li = facts.get((LEASE_INTEREST, fy))
    if not usable(li):
        return f"interest on lease liabilities for {fy} is not verified, so the lease check cannot be made"
    if finance_costs_fy is None:
        return f"finance costs for the fiscal year to {fy} are not loaded, so the lease check cannot be made"
    if li["value"] > finance_costs_fy:
        return (f"interest on lease liabilities for {fy} (Rs {li['value'] / 1e7:,.0f} crore) is larger than finance costs "
                f"for the 4 quarters to {fy} (Rs {finance_costs_fy / 1e7:,.0f} crore); the rule that EBIT already adds back "
                "lease interest does not hold, so no lease-adjusted figures")
    return None


def lease_text(*leases: Lease) -> str:
    parts = [f"Rs {x.value / 1e7:,.0f} crore as of {x.fiscal_year_end}" for x in leases]
    return "lease liabilities " + " and ".join(parts) + ", from the annual report"


# ---------------------------------------------------------------- gold on loan

def has_gold_rows(facts: dict) -> bool:
    return any(item == GOLD for (item, _) in facts)


def gold_series(facts: dict) -> dict[date, float]:
    """Balance sheet date -> verified gold on loan."""
    return {d: row["value"] for (i, d), row in facts.items() if i == GOLD and usable(row)}


def gold_on(series: dict[date, float], dates: list[date]) -> tuple[list[float] | None, date | None]:
    """EXACT DATE RULE: a figure for each date, or (None, the first date with no figure). No carry forward:
    gold on loan moves with the gold price and the stock held."""
    for d in dates:
        if d not in series:
            return None, d
    return [series[d] for d in dates], None


def gold_text(series: dict[date, float], dates: list[date]) -> str:
    return "gold on loan " + " and ".join(f"Rs {series[d] / 1e7:,.0f} crore as of {d}" for d in dates) + \
        ", from the balance sheet"


# ---------------------------------------------------------------- R&D

@dataclass
class RD:
    fy0: date
    r: tuple[float, float, float, float]  # R0..R3, R0 latest
    known: int  # how many of R0..R3 are verified figures
    earliest: date  # earliest verified year; earlier years assumed equal to it
    asset: float
    amortisation: float

    @property
    def ebit_change(self) -> float:
        return self.r[0] - self.amortisation


def rd_capitalise(series: dict[date, float]) -> RD | None:
    """series: fiscal year end -> verified R&D. Needs the latest year and the year before it."""
    if not series:
        return None
    fy0 = max(series)
    years = [date(fy0.year - k, fy0.month, fy0.day) for k in range(RD_LIFE + 1)]
    if years[1] not in series:
        return None
    known, r = 0, []
    for y in years:
        if y in series and known == len(r):  # consecutive known years only
            r.append(series[y]); known += 1
        else:
            r.append(r[-1])  # assumed equal to the earliest known year
    r0, r1, r2, r3 = r
    asset = r0 + r1 * 2 / 3 + r2 * 1 / 3
    amort = (r1 + r2 + r3) / 3
    return RD(fy0, (r0, r1, r2, r3), known, years[known - 1], asset, amort)


def rd_series(facts: dict, item: str) -> dict[date, float]:
    return {fy: row["value"] for (i, fy), row in facts.items() if i == item and usable(row)}


def rd_inputs(facts: dict, item: str, roic_date: date) -> tuple[RD | None, str | None]:
    """R&D capitalisation for the ROIC balance sheet date, or the reason it is not done."""
    series = {fy: v for fy, v in rd_series(facts, item).items() if fy <= roic_date}
    if not series:
        return None, f"no verified {item} figures"
    rd = rd_capitalise(series)
    if rd is None:
        return None, f"{item}: only {_fy(max(series))} is verified; at least 2 consecutive years are needed"
    if (roic_date - rd.fy0).days > MAX_AGE_DAYS:
        return None, f"{item}: latest year {rd.fy0} is more than {MAX_AGE_DAYS} days before {roic_date}"
    return rd, None


def rd_caveat(rd: RD) -> str:
    if rd.known >= RD_LIFE + 1:
        hist = f"{rd.known} years of R&D figures"
    else:
        hist = f"only {rd.known} years of R&D figures; earlier years assumed equal to {_fy(rd.earliest)}"
    return f"{hist}; latest fiscal year R&D set against 12-month EBIT"


def roic_rd(ebit: float, tax_rate: float, ic_incl_leases: float, rd: RD) -> tuple[float, float, float] | None:
    """(ROIC with R&D capitalised, adjusted NOPAT, adjusted invested capital). No tax on the adjustment."""
    nopat = ebit * (1 - tax_rate) + rd.ebit_change
    ic = ic_incl_leases + rd.asset
    return (nopat / ic, nopat, ic) if ic > 0 else None
