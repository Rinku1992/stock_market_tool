"""Valuation stage A (equity research desk). Code only, facts only: every view is neutral.

Three analysts, from NSE results filings and stored prices, as known at the as_of market close:
    earnings_power      reported vs core profit, owner earnings, free cash flow, cash conversion, accruals
    capital_returns     ROIC and ROE (lenders: ROE, return on assets, credit cost)
    relative_valuation  market value, EV, P/E, earnings yield, EV/EBITDA, EV/Sales, FCF yield,
                        dividend yield, P/B next to ROE

Framework and stages: docs/VALUATION.md. Rules for the data (see DECISIONS.md):
- 12-month (TTM) P&L = 4 consecutive quarters. Cash flow is filed only for the half year (Sep) and
  full year (March), so TTM cash flow to Sep = half year to Sep + year to March - half year to last Sep.
  When cash flow is set against profit, both cover the same 12 months.
- Balance sheets exist only for Sep and March. "Average" = closing and the one a year earlier.
- Lease liabilities are not in quarterly filings. Stage B adds them from verified annual report facts
  (adjustments.py): EV and invested capital "including leases" lead, the stage A figure follows as
  "before leases". Without verified lease facts, figures are before leases and say so. R&D is
  capitalised (rough) in capital_returns when verified consolidated R&D exists.
- Missing is never zero. A filed zero counts as real only where zero is plausible (dividends,
  minority interest, borrowings, capex); never for shares, face value, equity, revenue, total assets.
- Share count = paid-up capital / face value, cleaned against the balance sheet's share capital.
"""

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, timedelta

from smt.analysts import adjustments as adj
from smt.analysts.base import AnalystContext
from smt.data.financials import load_financials
from smt.data.prices import load_bars
from smt.router import LENDER, REAL_ESTATE, StockType, stock_type_for
from smt.schema import AnalystKind, AnalystReport, Evidence, neutral_views

UNIT = "equity_research"
CRORE = 1e7
SHARE_MISMATCH = 0.02  # paid-up capital vs balance-sheet share capital
SHARE_JUMP = 0.20  # change in share count between filings that means a likely split or bonus
PRICE_STALE_DAYS = 7
CASH_LABEL = "cash, bank balances and current investments"
MUST_BE_POSITIVE = {"revenue", "total_assets", "equity", "equity_owners", "share_capital", "paid_up_capital", "face_value"}


def _cr(v: float | None) -> str:
    return "n/a" if v is None else f"Rs {v / CRORE:,.0f} crore"


def _pc(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.1%}"


def _x(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.1f}x"


def _month_end(y: int, m: int) -> date:
    while m < 1:
        y, m = y - 1, m + 12
    return date(y, m, monthrange(y, m)[1])


def _ratio(a: float | None, b: float | None) -> float | None:
    return None if a is None or not b else a / b


@dataclass
class Fin:
    """The filed numbers for one stock, one basis, as known on as_of."""
    rows: list[dict]
    basis: str
    by: dict = field(default_factory=dict)

    def __post_init__(self):
        for r in self.rows:
            self.by[(r["period_end"], r["period_type"], r["line_item"])] = r["value"]

    def get(self, end: date, pt: str, item: str) -> float | None:
        v = self.by.get((end, pt, item))
        if v is not None and item in MUST_BE_POSITIVE and v <= 0:
            return None  # a zero here is an unfilled tag, not a real value
        return v

    def quarters(self) -> list[date]:
        return sorted({e for (e, pt, item) in self.by if pt == "Q" and item == "revenue"})

    def bs_dates(self) -> list[date]:
        return sorted({e for (e, pt, item) in self.by if pt == "I" and item in ("equity", "total_assets")})

    def ttm(self, end: date, item: str) -> tuple[float | None, list[date]]:
        """Sum of 4 consecutive quarters ending at `end`; also the quarters that are missing."""
        ends = [_month_end(end.year, end.month - 3 * k) for k in range(4)]
        vals = [self.get(e, "Q", item) for e in ends]
        missing = [e for e, v in zip(ends, vals) if v is None]
        return (None if missing else sum(vals)), missing

    def cf_end(self) -> date | None:
        ends = [e for (e, pt, item) in self.by if item == "cfo" and pt in ("H", "A")]
        return max(ends) if ends else None

    def cf_ttm(self, item: str) -> tuple[float | None, str]:
        """12-month cash flow item ending at the latest cash flow date, and how it was built."""
        c = self.cf_end()
        if c is None:
            return None, "no cash flow statement loaded"
        if (c, "A") in {(e, pt) for (e, pt, i) in self.by if i == "cfo"}:
            return self.get(c, "A", item), f"year to {c}"
        mar, prev = date(c.year, 3, 31), date(c.year - 1, c.month, c.day)
        parts = {"half year to " + str(c): self.get(c, "H", item), "year to " + str(mar): self.get(mar, "A", item),
                 "half year to " + str(prev): self.get(prev, "H", item)}
        missing = [k for k, v in parts.items() if v is None]
        if missing:
            return None, "missing " + ", ".join(missing)
        h, a, hp = parts.values()
        return h + a - hp, f"12 months to {c} (half year + last full year - last half year)"

    def opening(self, end: date) -> date | None:
        for d in self.bs_dates():
            if abs((end - d).days - 365) <= 10:
                return d
        return None

    def avg(self, end: date, item: str) -> tuple[float | None, str]:
        close, op = self.get(end, "I", item), self.opening(end)
        open_ = self.get(op, "I", item) if op else None
        if close is None:
            return None, "not filed"
        if open_ is None:
            return close, f"closing {end} only (no balance sheet a year earlier)"
        return (close + open_) / 2, f"average of {op} and {end}"


def load_fin(ctx: AnalystContext) -> Fin | None:
    """Consolidated if the stock files it, else standalone."""
    rows = load_financials(ctx.lake, ctx.instrument_id, ctx.as_of, consolidated=True)
    if any(r["line_item"] == "revenue" for r in rows):
        return Fin(rows, "consolidated")
    rows = load_financials(ctx.lake, ctx.instrument_id, ctx.as_of, consolidated=False)
    return Fin(rows, "standalone") if any(r["line_item"] == "revenue" for r in rows) else None


def share_count(f: Fin) -> tuple[float | None, date | None, list[str]]:
    """Latest usable share count, its filing date, and flags.

    1. count = paid-up capital / face value from each filing's quarter.
    2. Where the filing also has a balance sheet, its share capital must agree within 2%; if not, the
       balance-sheet figure is used and the filing is flagged.
    3. June and December filings have no balance sheet: if paid-up capital disagrees by more than 2%
       with the nearest balance sheet, the filing is flagged and its count is not used.
    4. A change of more than 20% between filings is a likely split or bonus: only counts from after it are used.
    """
    flags, counts = [], []
    sc_dates = [d for d in f.bs_dates() if f.get(d, "I", "share_capital") is not None]
    for e in sorted({e for (e, pt, i) in f.by if pt == "Q" and i == "paid_up_capital"}):
        pu, fv = f.get(e, "Q", "paid_up_capital"), f.get(e, "Q", "face_value")
        if pu is None or fv is None:
            continue
        sc = f.get(e, "I", "share_capital")
        if sc is not None:
            if abs(pu / sc - 1) > SHARE_MISMATCH:
                flags.append(f"filing for {e}: paid-up capital {_cr(pu)} disagrees with balance-sheet share capital "
                             f"{_cr(sc)}; the balance-sheet figure is used")
                pu = sc
        elif sc_dates:
            near = min(sc_dates, key=lambda d: abs((d - e).days))
            ref = f.get(near, "I", "share_capital")
            if abs(pu / ref - 1) > SHARE_MISMATCH:
                flags.append(f"filing for {e}: paid-up capital {_cr(pu)} disagrees with share capital {_cr(ref)} on the "
                             f"nearest balance sheet ({near}); this filing's share count is not used")
                continue
        counts.append((e, pu / fv))
    for (d0, n0), (d1, n1) in zip(counts, counts[1:]):
        if abs(n1 / n0 - 1) > SHARE_JUMP:
            flags.append(f"share count changed {n1 / n0 - 1:+.0%} between {d0} and {d1}: likely a split or bonus, "
                         f"so only share counts from {d1} on are used")
            counts = [(d, n) for d, n in counts if d >= d1]
    if not counts:
        return None, None, flags
    return counts[-1][1], counts[-1][0], flags


@dataclass
class Base:
    """What all three analysts start from."""
    fin: Fin
    st: StockType
    q: date  # latest quarter
    gaps: list[str]


def _base(ctx: AnalystContext) -> Base | None:
    fin = load_fin(ctx)
    if fin is None or not fin.quarters():
        return None
    return Base(fin, stock_type_for(ctx.lake, ctx.instrument_id, ctx.as_of), fin.quarters()[-1],
                ["direction rule: facts only (valuation stage A); no rule tested yet"])


def _ttm(b: Base, end: date, item: str, label: str, need: list[str]) -> float | None:
    v, missing = b.fin.ttm(end, item)
    if v is None:
        need.append(f"{label}: quarter(s) {', '.join(map(str, missing))} not loaded or not filed")
    return v


def _report(ctx, analyst_id, finding, b: Base | None, gaps, features=None) -> AnalystReport:
    for name, value in (features or {}).items():
        if value is not None:
            ctx.store.put_feature(symbol=ctx.instrument_id, name=name, value=float(value), known_at=ctx.as_of,
                                  origin="code", source=analyst_id)
    ev = []
    if b is not None:
        known = max(r["published_at"] for r in b.fin.rows).date()
        ev = [Evidence(id=f"financials:{ctx.instrument_id}:{b.q}", source_type="filing",
                       ref=f"financials/{ctx.instrument_id}/{b.q}", as_of=min(known, ctx.as_of))]
    return AnalystReport(analyst_id=analyst_id, unit=UNIT, kind=AnalystKind.CODE, symbol=ctx.symbol, as_of=ctx.as_of,
                         finding=finding, views=neutral_views(), evidence=ev, could_not_check=gaps)


def _crs(v: float) -> str:
    """Signed crore amount, e.g. +Rs 1 crore."""
    return ("+" if v >= 0 else "-") + _cr(abs(v))


def fy_finance_costs(fin: Fin, fy: date) -> tuple[float | None, str]:
    """Finance costs for the fiscal year ending fy: the 4 quarters added up, or, when a quarter is not
    loaded, the full-year figure filed with the March results (the same 12 months)."""
    v, missing = fin.ttm(fy, "finance_costs")
    if v is not None:
        return v, "4 quarters added up"
    full = fin.get(fy, "A", "finance_costs")
    if full is not None:
        return full, f"full-year figure filed for {fy} (quarter {', '.join(map(str, missing))} not loaded)"
    return None, f"quarter(s) {', '.join(map(str, missing))} not loaded and no full-year figure"


def _lease_figures(b: Base, facts: dict, dates: list[date]) -> tuple[list[adj.Lease] | None, str | None]:
    """Lease figure for each balance sheet date, after the lease interest check; or the reason there is none."""
    totals = adj.lease_totals(facts)
    if not totals:
        return None, "no lease liabilities from an annual report"
    leases = [adj.lease_on(totals, d) for d in dates]
    for x in leases:
        if x.value is None:
            return None, x.why
    fy = leases[0].fiscal_year_end
    fc, _ = fy_finance_costs(b.fin, fy)
    why = adj.lease_interest_check(facts, fy, fc)
    return (None, why) if why else (leases, None)


LEASE_FORMULA = ("Including leases, lease liabilities (current + non-current) from the annual report are added; EBIT "
                 "and EBITDA are not changed, because lease interest is inside finance costs, which EBIT already adds "
                 "back, and right-of-use depreciation is inside depreciation")


def _no_data(ctx, analyst_id):
    return _report(ctx, analyst_id, "No quarterly results known for this stock as of this date.", None,
                   ["financials: no results known as of this date"])


OI_NOTE = "other income may include interest from joint ventures and project partners (not verified)"


def _ebit(b: Base, end: date, need: list[str], with_other_income: bool = False) -> float | None:
    """Operating EBIT = profit before tax and exceptional items + finance costs - other income.
    with_other_income=True keeps other income in (used to lead for real estate)."""
    p = _ttm(b, end, "pbt_before_exceptional", "profit before exceptional items", need)
    fc = _ttm(b, end, "finance_costs", "finance costs", need)
    if with_other_income:
        return None if None in (p, fc) else p + fc
    oi = _ttm(b, end, "other_income", "other income", need)
    return None if None in (p, fc, oi) else p + fc - oi


# ---------------------------------------------------------------- earnings power

def earnings_power(ctx: AnalystContext) -> AnalystReport:
    b = _base(ctx)
    if b is None:
        return _no_data(ctx, "earnings_power")
    gaps, need, feats = list(b.gaps), [], {}
    parts = [b.st.describe(), f"12 months to {b.q} ({b.fin.basis}):"]
    np_ = _ttm(b, b.q, "net_profit", "reported profit", need)
    own = _ttm(b, b.q, "net_profit_owners", "owners' profit", need)
    ex, _ = b.fin.ttm(b.q, "exceptional_items")
    core = np_ - ex if np_ is not None and ex is not None else None
    parts.append(f"reported profit {_cr(np_)}, owners' share {_cr(own)}.")
    if core is not None:
        parts.append(f"Core profit {_cr(core)} (computed by us: reported profit minus exceptional items of {_cr(ex)}, "
                     "before their tax effect).")
        feats["val_core_profit_ttm"] = core
    else:
        gaps.append("core profit: exceptional items not filed for all 4 quarters")
    feats.update({"val_profit_ttm": np_, "val_owners_profit_ttm": own})

    if b.st.label == LENDER:
        parts.append("Owner earnings, free cash flow, cash conversion and accruals are not used for a lender: "
                     "giving out loans is counted as operating cash outflow, so they say nothing about earnings quality.")
        return _report(ctx, "earnings_power", " ".join(parts), b, gaps + need, feats)

    c = b.fin.cf_end()
    cfo, how = b.fin.cf_ttm("cfo")
    if c is None or cfo is None:
        gaps.append(f"cash flow: {how}")
        return _report(ctx, "earnings_power", " ".join(parts), b, gaps + need, feats)
    capex_ppe, _ = b.fin.cf_ttm("capex")
    capex_int, _ = b.fin.cf_ttm("capex_intangibles")
    capex = None if capex_ppe is None else capex_ppe + (capex_int or 0.0)
    if capex_ppe is not None and capex_int is None:
        gaps.append("capex: purchase of intangible assets not filed; capex is property, plant and equipment only")
    np_c = _ttm(b, c, "net_profit", f"profit for the 12 months to {c}", need)
    own_c = _ttm(b, c, "net_profit_owners", f"owners' profit for the 12 months to {c}", need)
    dep_c = _ttm(b, c, "depreciation", f"depreciation for the 12 months to {c}", need)
    line = f"Cash flow, {how}: operating cash {_cr(cfo)}, capex {_cr(capex)}"
    if capex is not None:
        fcf = cfo - capex
        line += f", free cash flow {_cr(fcf)}"
        feats["val_fcf_ttm"] = fcf
        if own_c is not None and dep_c is not None:
            oe = own_c + dep_c - capex
            line += (f". Owner earnings {_cr(oe)} (computed by us: owners' profit + depreciation - capex; capex is TOTAL "
                     "capex, not maintenance only, so this understates owner earnings for a growing company)")
            feats["val_owner_earnings_ttm"] = oe
    else:
        gaps.append("capex: not filed")
    parts.append(line + ".")
    if np_c:
        conv = cfo / np_c
        parts.append(f"Cash conversion {conv:.2f} (operating cash / reported profit, same 12 months).")
        feats["val_cash_conversion"] = conv
    ta, how_ta = b.fin.avg(c, "total_assets")
    if np_c is not None and ta:
        acc = (np_c - cfo) / ta
        parts.append(f"Accruals {acc:+.1%} of total assets (computed by us: (profit - operating cash) / total assets, "
                     f"{how_ta}). Large positive accruals mean profit is running ahead of cash.")
        feats["val_accruals"] = acc
    return _report(ctx, "earnings_power", " ".join(parts), b, gaps + need, feats)


# ---------------------------------------------------------------- capital returns

def _cash(fin: Fin, d: date) -> tuple[float | None, list[str]]:
    """Cash, bank balances and current investments on a balance-sheet date. Cash itself must be filed."""
    cash = fin.get(d, "I", "cash")
    if cash is None:
        return None, ["cash"]
    other = [fin.get(d, "I", k) for k in ("other_bank_balances", "current_investments")]
    missing = [n for n, v in zip(("other bank balances", "current investments"), other) if v is None]
    return cash + sum(v for v in other if v is not None), missing


def _invested(fin: Fin, d: date) -> tuple[float | None, list[str]]:
    """Total equity + borrowings - cash, bank balances and current investments. Also the parts not filed."""
    eq = fin.get(d, "I", "equity")
    debt = [fin.get(d, "I", k) for k in ("borrowings_noncurrent", "borrowings_current")]
    cash, cash_missing = _cash(fin, d)
    missing = [f"{n} on {d}" for n, bad in (("total equity", eq is None), ("borrowings", None in debt), ("cash", cash is None)) if bad]
    if missing:
        return None, missing
    return eq + sum(debt) - cash, [f"{m} on {d} (left out of cash)" for m in cash_missing]


def capital_returns(ctx: AnalystContext) -> AnalystReport:
    b = _base(ctx)
    if b is None:
        return _no_data(ctx, "capital_returns")
    gaps, need, feats = list(b.gaps), [], {}
    bs = b.fin.bs_dates()
    if not bs:
        return _report(ctx, "capital_returns", b.st.describe() + " No balance sheet loaded.", b, gaps + ["balance sheet: not loaded"])
    end = bs[-1]
    parts = [b.st.describe(), f"12 months to {end} ({b.fin.basis}):"]
    own = _ttm(b, end, "net_profit_owners", "owners' profit", need)
    eq_o, how_eq = b.fin.avg(end, "equity_owners")
    roe = _ratio(own, eq_o) if eq_o and eq_o > 0 else None
    parts.append(f"ROE {_pc(roe)} (owners' profit {_cr(own)} / owners' equity, {how_eq}).")
    feats["val_roe"] = roe

    if b.st.label == LENDER:
        ta, how_ta = b.fin.avg(end, "total_assets")
        roa = _ratio(own, ta)
        parts.append(f"Return on assets {_pc(roa)} (owners' profit / total assets, {how_ta}).")
        cc = _ttm(b, end, "credit_cost", "credit cost", need)
        loans, how_l = b.fin.avg(end, "loans")
        if cc is not None:
            parts.append(f"Credit cost {_cr(cc)} over 12 months" + (f", {cc / loans:.2%} of loans ({how_l})" if loans else "") + ".")
        parts.append("ROIC is not used for a lender: borrowed money is its raw material, not financing, so "
                     "invested capital has no clean meaning.")
        feats.update({"val_roa": roa, "val_credit_cost_ttm": cc})
        return _report(ctx, "capital_returns", " ".join(parts), b, gaps + need, feats)

    re = b.st.label == REAL_ESTATE
    ebit = _ebit(b, end, need, with_other_income=re)
    ebit_ex = _ebit(b, end, [], with_other_income=False) if re else None
    tax, pbt = _ttm(b, end, "tax", "tax", need), _ttm(b, end, "pbt", "profit before tax", need)
    t = tax / pbt if tax is not None and pbt and pbt > 0 else None
    if t is not None and not 0 <= t <= 1:
        gaps.append(f"tax rate: {t:.0%} is outside 0-100%, so ROIC is not computed")
        t = None
    ic_c, miss_c = _invested(b.fin, end)
    op = b.fin.opening(end)
    ic_o, miss_o = _invested(b.fin, op) if op else (None, [])
    ic = (ic_c + ic_o) / 2 if ic_c is not None and ic_o is not None else ic_c
    how_ic = f"average of {op} and {end}" if ic_o is not None and ic_c is not None else f"closing {end} only"
    for m in sorted(set(miss_c + miss_o)):
        gaps.append(f"invested capital: {m} not filed")
    ebit_def = ("EBIT = profit before tax and exceptional items + finance costs (other income kept in, for real estate)"
                if re else "EBIT = profit before tax and exceptional items + finance costs - other income")
    formula_core = ("ROIC = EBIT x (1 - effective tax rate) / (total equity + borrowings - cash, bank balances and current "
                    f"investments), with {ebit_def}")
    formula = formula_core + ". Lease liabilities are not in quarterly filings, so they are left out of invested capital"
    if ebit is not None and t is not None and ic and ic > 0:
        roic = ebit * (1 - t) / ic
        label = "EBIT including other income" if re else "EBIT"
        line = f"ROIC {_pc(roic)}: {label} {_cr(ebit)}, tax rate {t:.0%}, invested capital {_cr(ic)} ({how_ic})."
        if re:
            line += (f" Excluding other income: EBIT {_cr(ebit_ex)}"
                     + (f", ROIC {_pc(ebit_ex * (1 - t) / ic)}" if ebit_ex is not None else "") + f"; {OI_NOTE}.")
            feats["val_ebit_ex_oi_ttm"] = ebit_ex
        feats.update({"val_roic": roic, "val_ebit_ttm": ebit})
        facts = adj.facts_asof(ctx.lake, ctx.instrument_id, ctx.as_of)
        ic_l = None
        dates = [end] + ([op] if ic_o is not None else [])
        leases, why = _lease_figures(b, facts, dates)
        if leases:
            ic_l = sum(icx + x.value for icx, x in zip([ic_c, ic_o], leases)) / len(leases)
        ic_lead = None
        if ic_l and ic_l > 0:
            roic_l = ebit * (1 - t) / ic_l
            ic_lead = ic_l
            lease_line = (f"ROIC including leases {_pc(roic_l)}: {label} {_cr(ebit)}, tax rate {t:.0%}, invested capital "
                          f"including leases {_cr(ic_l)} ({how_ic}; {adj.lease_text(*leases)}).")
            gold, fact_line = _gold(facts, dates, "ROIC", gaps)
            if gold:
                ic_g = sum(icx + x.value + g for icx, x, g in zip([ic_c, ic_o], leases, gold)) / len(leases)
                if ic_g > 0:
                    roic_g = ebit * (1 - t) / ic_g
                    ic_lead = ic_g
                    parts.append(f"ROIC including leases and gold on loan {_pc(roic_g)}: {label} {_cr(ebit)}, tax rate "
                                 f"{t:.0%}, invested capital including leases and gold on loan {_cr(ic_g)} ({how_ic}; "
                                 f"{adj.gold_text(adj.gold_series(facts), dates)}); {adj.GOLD_NOTE}.")
                    feats.update({"val_gold_on_loan": gold[0], "val_roic_incl_leases_gold": roic_g})
                    lease_line = "Including leases: " + lease_line
                else:
                    gaps.append(f"gold on loan: invested capital including leases and gold on loan is {_cr(ic_g)}, not positive")
            parts.append(lease_line)
            parts.append(f"Before leases: {line} {formula_core}. {LEASE_FORMULA}.")
            if fact_line:
                parts.append(fact_line)
            feats.update({"val_lease_liabilities": leases[0].value, "val_roic_incl_leases": roic_l})
        else:
            if leases:
                why = f"invested capital including leases is {_cr(ic_l)}, not positive"
            gaps.append(f"leases: {why}; ROIC is before leases")
            if adj.has_gold_rows(facts):
                gaps.append("gold on loan: lease figures are not available, so it is not added")
            parts.append(f"{line} {formula}.")
        rev_end, _ = b.fin.ttm(end, "revenue")
        parts += _rd_lines(b, facts, end, ebit, t, ic_lead, gaps, feats, rev_end)
    elif ic is not None and ic <= 0:
        parts.append(f"ROIC not meaningful: invested capital is {_cr(ic)} (cash exceeds equity plus debt). {formula}.")
    else:
        parts.append(f"ROIC not computed (see notes). {formula}.")
    return _report(ctx, "capital_returns", " ".join(parts), b, gaps + need, feats)


def _gold(facts: dict, dates: list[date], what: str, gaps: list) -> tuple[list[float] | None, str | None]:
    """Gold on loan for every date (exact date rule), or None with a fact line and a reason.
    No gold_on_loan rows at all: (None, None) and nothing is said."""
    if not adj.has_gold_rows(facts):
        return None, None
    series = adj.gold_series(facts)
    if not series:
        gaps.append("gold on loan: no verified figure")
        return None, None
    gold, missing = adj.gold_on(series, dates)
    if gold:
        return gold, None
    latest = max(series)
    gaps.append(f"gold on loan: no verified figure for {missing} (exact date rule: no carry forward); {what} is not "
                "adjusted for it")
    return None, (f"Gold on loan Rs {series[latest] / 1e7:,.0f} crore as of {latest}; not in EV or ROIC: no figure for "
                  f"{missing}.")


WIDE_NOTE = "includes innovation spend, which is not R&D by the accounting definition"


def _rd_lines(b: Base, facts: dict, end: date, ebit: float, t: float, ic_l: float | None, gaps: list, feats: dict,
              revenue: float | None = None) -> list[str]:
    """Rough ROIC with R&D capitalised: narrow (R&D) leads, wide (R&D and innovation) next to it.
    ic_l is the lead invested capital (including leases, and gold on loan when present). Under 1% of the
    12-month revenue to the ROIC date, R&D gets one short "not material" sentence."""
    has_rows = any(i in (adj.RD_NARROW, adj.RD_WIDE) for (i, _) in facts)
    if b.fin.basis != "consolidated":
        if has_rows:
            gaps.append("R&D: valuation basis is standalone; R&D is capitalised only on a consolidated basis")
        return []
    if not adj.rd_series(facts, adj.RD_NARROW) and not adj.rd_series(facts, adj.RD_WIDE):
        gaps.append("R&D: no verified consolidated R&D figures in the annual report facts; not capitalised")
        return []
    out = []
    for item, key, name, what in ((adj.RD_NARROW, "val_roic_rd_rough", "narrow: R&D only", "R&D"),
                                  (adj.RD_WIDE, "val_roic_rd_wide_rough", f"wide: R&D and innovation; {WIDE_NOTE}",
                                   "R&D and innovation spend")):
        rd, why = adj.rd_inputs(facts, item, end)
        if rd is None:
            gaps.append(f"R&D ({name.split(':')[0]}): {why}; not capitalised")
            continue
        if ic_l is None:
            gaps.append(f"R&D ({name.split(':')[0]}): invested capital including leases is not available; not capitalised")
            continue
        got = adj.roic_rd(ebit, t, ic_l, rd)
        if got is None:
            gaps.append(f"R&D ({name.split(':')[0]}): adjusted invested capital is not positive; not computed")
            continue
        roic, nopat, ic = got
        share = rd.r[0] / revenue if revenue and revenue > 0 else None
        if share is not None and share < adj.RD_MATERIAL:
            out.append(f"{what} is {share:.1%} of revenue: not material for this company (rough ROIC with it "
                       f"capitalised: {_pc(roic)}).")
        else:
            s = (f"Rough ROIC with R&D capitalised {_pc(roic)} ({name}): research asset {_cr(rd.asset)} "
                 f"(R0 + 2/3 x R1 + 1/3 x R2, 3-year life, R0 = {rd.fy0}), amortisation {_cr(rd.amortisation)}, "
                 f"EBIT change {_crs(rd.ebit_change)}, adjusted NOPAT {_cr(nopat)}, adjusted invested capital {_cr(ic)}; "
                 f"rough: {adj.rd_caveat(rd)}.")
            if share is None:
                gaps.append(f"R&D ({name.split(':')[0]}): 12-month revenue to {end} not available, so its share of revenue "
                            "is not shown")
            else:
                s += f" {what} is {share:.1%} of revenue (latest fiscal year against 12 months to {end})."
            out.append(s)
        feats[key] = roic
        if item == adj.RD_NARROW:
            feats["val_rd_asset_rough"] = rd.asset
    return out


# ---------------------------------------------------------------- relative valuation

def relative_valuation(ctx: AnalystContext) -> AnalystReport:
    b = _base(ctx)
    if b is None:
        return _no_data(ctx, "relative_valuation")
    gaps, need, feats = list(b.gaps), [], {}
    shares, sh_date, flags = share_count(b.fin)
    bars = load_bars(ctx.lake, ctx.instrument_id, ctx.as_of)
    if shares is None or not bars:
        gaps.append("market value: " + ("no usable share count" if shares is None else "no price loaded"))
        return _report(ctx, "relative_valuation", b.st.describe() + " Market value not computed.", b, gaps + flags)
    px = bars[-1]
    if (ctx.as_of - px.d).days > PRICE_STALE_DAYS:
        gaps.append(f"price: latest stored close is {px.d}, more than {PRICE_STALE_DAYS} days old")
    mcap = shares * px.close
    parts = [b.st.describe(), f"Market value {_cr(mcap)} ({shares / CRORE:,.2f} crore shares from the filing for {sh_date} "
                              f"x close Rs {px.close:,.2f} on {px.d}). Basis: {b.fin.basis}."]
    feats["val_market_cap"] = mcap
    gaps += [f"share count: {fl}" for fl in flags]

    own = _ttm(b, b.q, "net_profit_owners", "owners' profit", need)
    np_, _ = b.fin.ttm(b.q, "net_profit")
    ex, _ = b.fin.ttm(b.q, "exceptional_items")
    core_own = own - ex if own is not None and ex is not None else None  # exceptional items before tax effect
    bs = b.fin.bs_dates()
    end = bs[-1] if bs else None
    eq_o = b.fin.get(end, "I", "equity_owners") if end else None
    pb = mcap / eq_o if eq_o else None
    own_b = _ttm(b, end, "net_profit_owners", "owners' profit to the balance-sheet date", []) if end else None
    eq_avg, _ = b.fin.avg(end, "equity_owners") if end else (None, "")
    roe = _ratio(own_b, eq_avg) if eq_avg and eq_avg > 0 else None
    pb_line = (f"P/B {_x(pb)} (owners' equity {_cr(eq_o)} on {end}) next to ROE {_pc(roe)}." if pb is not None
               else "P/B not computed: owners' equity not filed.")
    feats["val_pb"] = pb

    def pe_line() -> str:
        if own is None:
            return "P/E not computed: owners' profit missing for the last 4 quarters."
        if own <= 0:
            return f"No P/E: owners' profit over the 12 months to {b.q} is a loss ({_cr(own)})."
        pe = mcap / own
        feats.update({"val_pe": pe, "val_earnings_yield": own / mcap})
        s = f"P/E {_x(pe)} on reported owners' profit {_cr(own)} (12 months to {b.q}), earnings yield {_pc(own / mcap)}"
        if core_own is not None and core_own > 0:
            s += f"; core P/E {_x(mcap / core_own)} (before exceptional items)"
            feats["val_pe_core"] = mcap / core_own
        return s + "."

    if b.st.label == REAL_ESTATE:
        parts.append(pb_line + " P/B leads for real estate.")
        parts.append(pe_line() + " Warning: real estate profit is lumpy (revenue is booked when projects are handed "
                                 "over), so P/E swings with the delivery schedule.")
    elif b.st.label == LENDER:
        parts.append(pb_line + " P/B leads for a lender.")
        parts.append(pe_line())
    else:
        parts.append(pe_line())
        parts.append(pb_line)

    c = b.fin.cf_end()
    divs, how_d = b.fin.cf_ttm("dividends_paid")
    if divs is not None:
        parts.append(f"Dividend yield {_pc(divs / mcap)} (dividends paid {_cr(divs)}, {how_d}"
                     + ("; filed as 0" if divs == 0 else "") + ").")
        feats["val_dividend_yield"] = divs / mcap
    else:
        gaps.append(f"dividend yield: {how_d}")

    if b.st.label == LENDER:
        parts.append("EV, EV/EBITDA, EV/Sales and FCF yield are not used for a lender: its debt is raw material, not "
                     "financing, and lending shows up as operating cash outflow.")
        return _report(ctx, "relative_valuation", " ".join(parts), b, gaps + need, feats)

    if end is not None:
        debt = [b.fin.get(end, "I", k) for k in ("borrowings_noncurrent", "borrowings_current")]
        mi = b.fin.get(end, "I", "minority_interest")
        cash, cash_missing = _cash(b.fin, end)
        if None in debt or cash is None:
            gaps.append("EV: borrowings or cash not filed on " + str(end))
        else:
            ev = mcap + sum(debt) + (mi or 0.0) - cash
            if mi is None:
                gaps.append("EV: minority interest not filed; left out")
            for m in cash_missing:
                gaps.append(f"EV: {m} not filed; left out of cash")
            rev = _ttm(b, b.q, "revenue", "revenue", need)
            re = b.st.label == REAL_ESTATE
            dep = _ttm(b, b.q, "depreciation", "depreciation", need)

            lead_ebitda = {}

            def ev_ebitda(with_oi: bool, label: str, lead: bool) -> str:
                ebit = _ebit(b, b.q, need if lead else [], with_other_income=with_oi)
                ebitda = ebit + dep if ebit is not None and dep is not None else None
                if lead:
                    lead_ebitda["v"] = ebitda
                if ebitda is None:
                    return ""
                if ebitda <= 0:
                    return f", EV/EBITDA {label}not meaningful (EBITDA is {_cr(ebitda)})"
                if lead:
                    feats["val_ev_ebitda"] = ev / ebitda
                return f", EV/EBITDA {label}{_x(ev / ebitda)} (EBITDA {_cr(ebitda)})"

            s = (f"Enterprise value {_cr(ev)} (market value + borrowings {_cr(sum(debt))} + minority interest {_cr(mi)} "
                 f"- {CASH_LABEL} {_cr(cash)} on {end}; lease liabilities are not in quarterly filings and are left out)")
            if re:
                s += ev_ebitda(True, "including other income ", lead=True)
                s += ev_ebitda(False, "excluding other income ", lead=False)
                s += f"; {OI_NOTE}"
            else:
                s += ev_ebitda(False, "", lead=True).replace("(EBITDA", "(operating EBITDA")
            if rev:
                s += f", EV/Sales {_x(ev / rev)}"
                feats["val_ev_sales"] = ev / rev
            feats["val_ev"] = ev
            facts = adj.facts_asof(ctx.lake, ctx.instrument_id, ctx.as_of)
            leases, why = _lease_figures(b, facts, [end])
            if leases:
                ev_l = ev + leases[0].value
                ebitda = lead_ebitda.get("v")
                lead = f"Enterprise value including leases {_cr(ev_l)} (enterprise value + {adj.lease_text(*leases)})"
                if ebitda is not None and ebitda > 0:
                    lead += f", EV/EBITDA including leases {_x(ev_l / ebitda)} (same EBITDA {_cr(ebitda)})"
                    feats["val_ev_ebitda_incl_leases"] = ev_l / ebitda
                elif ebitda is not None:
                    lead += f", EV/EBITDA including leases not meaningful (EBITDA is {_cr(ebitda)})"
                if rev:
                    lead += f", EV/Sales including leases {_x(ev_l / rev)}"
                    feats["val_ev_sales_incl_leases"] = ev_l / rev
                before = s.replace("; lease liabilities are not in quarterly filings and are left out", "")
                gold, fact_line = _gold(facts, [end], "EV", gaps)
                if gold:
                    ev_g = ev_l + gold[0]
                    g = (f"Enterprise value including leases and gold on loan {_cr(ev_g)} (enterprise value including "
                         f"leases + {adj.gold_text(adj.gold_series(facts), [end])})")
                    if ebitda is not None and ebitda > 0:
                        g += f", EV/EBITDA including leases and gold on loan {_x(ev_g / ebitda)} (same EBITDA)"
                        feats["val_ev_ebitda_incl_leases_gold"] = ev_g / ebitda
                    if rev:
                        g += f", EV/Sales including leases and gold on loan {_x(ev_g / rev)}"
                        feats["val_ev_sales_incl_leases_gold"] = ev_g / rev
                    parts.append(f"{g}; {adj.GOLD_NOTE}.")
                    lead = "Including leases: " + lead
                    feats.update({"val_ev_incl_leases_gold": ev_g, "val_gold_on_loan": gold[0]})
                parts.append(f"{lead}. Before leases: {before}. {LEASE_FORMULA}.")
                if fact_line:
                    parts.append(fact_line)
                feats.update({"val_ev_incl_leases": ev_l, "val_lease_liabilities": leases[0].value})
            else:
                gaps.append(f"leases: {why}; EV is before leases")
                if adj.has_gold_rows(facts):
                    gaps.append("gold on loan: lease figures are not available, so it is not added")
                parts.append(s + ".")
    cfo, how_c = b.fin.cf_ttm("cfo")
    capex, _ = b.fin.cf_ttm("capex")
    capex_int, _ = b.fin.cf_ttm("capex_intangibles")
    if cfo is not None and capex is not None:
        fcf = cfo - capex - (capex_int or 0.0)
        parts.append(f"FCF yield {_pc(fcf / mcap)} (free cash flow {_cr(fcf)}, {how_c}).")
        feats["val_fcf_yield"] = fcf / mcap
    else:
        gaps.append(f"FCF yield: {how_c if cfo is None else 'capex not filed'}")
    return _report(ctx, "relative_valuation", " ".join(parts), b, gaps + need, feats)
