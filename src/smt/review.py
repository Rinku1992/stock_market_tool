"""Independent review, code part: evidence auditor, number re-checker, consistency check. No LLM.

SEPARATE CODE PATH: the re-checker recomputes key figures from raw lake rows with its own SQL and its own
arithmetic. It does NOT import or call any analyst helper (smt.analysts.*) or the financials loader, so an
error in an analyst's helper cannot hide itself by being reused here. It only reads the analysts' stored
features (to compare) and their report text (for the consistency check).

1. Evidence auditor: every analyst report that is not a "not built yet" stub needs at least one evidence item,
   and each item must point to something that exists in the lake (a filing, a price series, a report fact)
   dated on or before as_of. A report that fails is set aside: its views count as neutral for the desk and
   the verdict, and the failure is listed. Its text stays on the page, marked "no valid source".
2. Number re-checker: market value, 12-month revenue, 12-month reported profit, P/E, P/B, ROE, EBIT,
   borrowings, cash, EV before leases (those a playbook drops are skipped). Tolerance: 0.5% for amounts;
   0.05 in the unit shown for ratios (P/E and P/B in x, ROE in percentage points). Critical: market value,
   12-month profit and EV. A critical fail is a review veto.
3. Consistency: a quantity printed by two analysts (borrowings, cash, net debt, profit, shares) under the same
   label for the same date must be equal. A clash is a fail.
"""

import re
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from smt.schema import AnalystReport, neutral_views

UTC = timezone.utc
AMOUNT_TOL = 0.005
RATIO_TOL = {"x": 0.05, "pct": 0.0005}  # 0.05x; 0.05 percentage points
CRITICAL = ("market value", "12-month reported profit", "EV before leases")
STUB = "not built yet"
NO_SOURCE = "no valid source"
LENDER, REAL_ESTATE = "lender", "real_estate"


@dataclass
class Check:
    name: str
    status: str  # "pass", "fail", "not checked"
    text: str
    critical: bool = False


@dataclass
class Review:
    checks: list[Check] = field(default_factory=list)  # re-checker and consistency
    audit: list[Check] = field(default_factory=list)  # evidence auditor, one per analyst
    set_aside: dict[str, str] = field(default_factory=dict)  # analyst id -> why
    veto: list[str] = field(default_factory=list)  # critical fails (desk set-aside vetoes are added by scoring)
    desk_reports: list = field(default_factory=list)  # desk reports after set-aside reports are made neutral

    def lines(self) -> list[Check]:
        """All results, fails first, then not checked, then passes."""
        order = {"fail": 0, "not checked": 1, "pass": 2}
        return sorted(self.audit + self.checks, key=lambda c: order[c.status])


# ---------------------------------------------------------------- raw lake reads (own SQL)

def _close_utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 10, 0, tzinfo=UTC)  # 15:30 IST


def _raw_financials(lake, iid: str, as_of: date) -> dict:
    """{(period_end, period_type, line_item): value}, latest version published by the as_of close; consolidated
    when the company files consolidated revenue, else standalone."""
    if lake is None or not lake.has("financials"):
        return {}
    rows = lake.query(
        f"""SELECT consolidated, period_end, period_type, line_item, value, epoch(published_at)
            FROM {lake.read_sql('financials')} WHERE instrument_id = ? AND published_at <= ?""",
        [iid, _close_utc(as_of)])
    has_cons = any(r[0] and r[3] == "revenue" for r in rows)
    latest: dict = {}
    for cons, pe, pt, item, v, pub in rows:
        if bool(cons) != has_cons:
            continue
        k = (pe, pt, item)
        if k not in latest or pub > latest[k][1]:
            latest[k] = (v, pub)
    return {k: v for k, (v, _) in latest.items()}


def _raw_close(lake, iid: str, as_of: date) -> tuple[float, date] | None:
    if lake is None or not lake.has("prices_daily"):
        return None
    rows = lake.query(
        f"""SELECT trade_date, close FROM {lake.read_sql('prices_daily')}
            WHERE instrument_id = ? AND trade_date <= ? AND published_at <= ? ORDER BY trade_date DESC LIMIT 1""",
        [iid, as_of, datetime(as_of.year, as_of.month, as_of.day, 23, 59, 59, tzinfo=UTC)])
    return (rows[0][1], rows[0][0]) if rows else None


def _month_end_back(d: date, months: int) -> date:
    y, m = d.year, d.month - months
    while m < 1:
        y, m = y - 1, m + 12
    return date(y, m, monthrange(y, m)[1])


class Raw:
    def __init__(self, by: dict):
        self.by = by

    def get(self, d, pt, item):
        return self.by.get((d, pt, item))

    def q(self) -> date | None:
        qs = [d for (d, pt, i) in self.by if pt == "Q" and i == "revenue"]
        return max(qs) if qs else None

    def bs(self) -> date | None:
        ds = [d for (d, pt, i) in self.by if pt == "I" and i in ("equity", "total_assets")]
        return max(ds) if ds else None

    def ttm(self, end: date, item: str) -> float | None:
        vals = [self.get(_month_end_back(end, 3 * k), "Q", item) for k in range(4)]
        return None if None in vals else sum(vals)

    def year_before(self, end: date) -> date | None:
        for (d, pt, i) in self.by:
            if pt == "I" and i == "equity_owners" and abs((end - d).days - 365) <= 10:
                return d
        return None


# ---------------------------------------------------------------- evidence auditor

def _evidence_ok(lake, e, iid: str, as_of: date) -> str | None:
    if e.as_of > as_of:
        return f"evidence {e.id} is dated {e.as_of}, after {as_of}"
    if lake is None:
        return "no lake to check the source against"
    parts = e.ref.split("/")
    if parts[0] == "financials" and len(parts) == 3:
        if not lake.has("financials"):
            return f"{e.ref}: no filings in the lake"
        n = lake.query(f"SELECT count(*) FROM {lake.read_sql('financials')} WHERE instrument_id = ? AND period_end = ? "
                       "AND published_at <= ?", [parts[1], date.fromisoformat(parts[2]), _close_utc(as_of)])[0][0]
        return None if n else f"{e.ref}: no such filing known by {as_of}"
    if parts[0] == "prices_daily" and len(parts) == 2:
        if not lake.has("prices_daily"):
            return f"{e.ref}: no prices in the lake"
        n = lake.query(f"SELECT count(*) FROM {lake.read_sql('prices_daily')} WHERE instrument_id = ? AND trade_date = ?",
                       [parts[1], e.as_of])[0][0]
        return None if n else f"{e.ref}: no price for {e.as_of}"
    if parts[0] == "report_facts" and len(parts) >= 3:
        if not lake.has("report_facts"):
            return f"{e.ref}: no report facts in the lake"
        n = lake.query(f"SELECT count(*) FROM {lake.read_sql('report_facts')} WHERE instrument_id = ? AND item = ?",
                       [parts[1], parts[2]])[0][0]
        return None if n else f"{e.ref}: no such report fact"
    return f"{e.ref}: source type not recognised"


def audit(lake, reports: list[AnalystReport], iid: str | None, as_of: date) -> tuple[list[Check], dict[str, str]]:
    checks, aside = [], {}
    for r in reports:
        if r.finding.strip() == STUB:
            continue
        if not r.evidence:
            why = "no evidence item"
        else:
            why = next((w for e in r.evidence if (w := _evidence_ok(lake, e, iid, as_of))), None)
        if why:
            aside[r.analyst_id] = why
            checks.append(Check(f"evidence: {r.analyst_id}", "fail", f"evidence {r.analyst_id}: {why}; views set aside"))
        else:
            checks.append(Check(f"evidence: {r.analyst_id}", "pass",
                                f"evidence {r.analyst_id}: {len(r.evidence)} source(s) found in the lake"))
    return checks, aside


# ---------------------------------------------------------------- number re-checker

def _show(v: float, unit: str) -> str:
    return {"cr": f"Rs {v / 1e7:,.2f} crore", "x": f"{v:.2f}x", "pct": f"{v:.2%}"}[unit]


def _compare(name: str, mine: float | None, stored: float | None, unit: str, why_missing: str = "") -> Check:
    crit = name in CRITICAL
    if stored is None:
        return Check(name, "not checked", f"re-check {name}: no stored figure from this run", crit)
    if mine is None:
        return Check(name, "not checked", f"re-check {name}: could not recompute ({why_missing or 'inputs missing'})",
                     crit)
    ok = (abs(mine - stored) <= AMOUNT_TOL * abs(stored)) if unit == "cr" else abs(mine - stored) <= RATIO_TOL[unit]
    tol = "0.5%" if unit == "cr" else ("0.05x" if unit == "x" else "0.05 points")
    return Check(name, "pass" if ok else "fail",
                 f"re-check {name}: recomputed {_show(mine, unit)}, stored {_show(stored, unit)} "
                 f"({'within' if ok else 'outside'} {tol})", crit)


def recheck(lake, store, iid: str, as_of: date, stock_type: str | None) -> list[Check]:
    raw = Raw(_raw_financials(lake, iid, as_of))

    def stored(name):
        got = store.get_feature_asof(iid, name, as_of)
        if got is None:
            return None
        known = got["known_at"].date() if hasattr(got["known_at"], "date") else got["known_at"]
        return got["value"] if known == as_of else None  # only this run's figure

    q, end = raw.q(), raw.bs()
    px = _raw_close(lake, iid, as_of)
    shares = None
    if q is not None:
        pu, fv = raw.get(q, "Q", "paid_up_capital"), raw.get(q, "Q", "face_value")
        shares = pu / fv if pu and fv else None
    mcap = shares * px[0] if shares and px else None
    profit = raw.ttm(q, "net_profit") if q else None
    own_q = raw.ttm(q, "net_profit_owners") if q else None
    out = [_compare("market value", mcap, stored("val_market_cap"), "cr",
                    "no share count" if not shares else "no price"),
           _compare("12-month reported profit", profit, stored("val_profit_ttm"), "cr")]
    pe = mcap / own_q if mcap and own_q and own_q > 0 else None
    out.append(_compare("P/E", pe, stored("val_pe"), "x"))
    eq_o = raw.get(end, "I", "equity_owners") if end else None
    out.append(_compare("P/B", mcap / eq_o if mcap and eq_o and eq_o > 0 else None, stored("val_pb"), "x"))
    roe = None
    if end:
        own_e, prev = raw.ttm(end, "net_profit_owners"), raw.year_before(end)
        eq_avg = (eq_o + raw.get(prev, "I", "equity_owners")) / 2 if eq_o and prev else eq_o
        roe = own_e / eq_avg if own_e is not None and eq_avg and eq_avg > 0 else None
    out.append(_compare("ROE", roe, stored("val_roe"), "pct"))
    if stock_type == LENDER:
        return out
    out.append(_compare("12-month revenue", raw.ttm(q, "revenue") if q else None, stored("val_revenue_ttm"), "cr"))
    ebit = None
    if end:
        p, fc, oi = (raw.ttm(end, k) for k in ("pbt_before_exceptional", "finance_costs", "other_income"))
        if stock_type == REAL_ESTATE:
            ebit = p + fc if None not in (p, fc) else None
        else:
            ebit = p + fc - oi if None not in (p, fc, oi) else None
    out.append(_compare("EBIT", ebit, stored("val_ebit_ttm"), "cr"))
    debt = cash = None
    if end:
        b = [raw.get(end, "I", k) for k in ("borrowings_noncurrent", "borrowings_current")]
        debt = None if None in b else sum(b)
        c = raw.get(end, "I", "cash")
        if c is not None:
            cash = c + sum(raw.get(end, "I", k) or 0.0 for k in ("other_bank_balances", "current_investments"))
    out.append(_compare("borrowings", debt, stored("val_borrowings"), "cr"))
    out.append(_compare("cash, bank balances and current investments", cash, stored("val_cash"), "cr"))
    ev = None
    if mcap is not None and debt is not None and cash is not None:
        ev = mcap + debt + (raw.get(end, "I", "minority_interest") or 0.0) - cash
    out.append(_compare("EV before leases", ev, stored("val_ev"), "cr"))
    return out


# ---------------------------------------------------------------- consistency between analysts

_DATE = r"\d{4}-\d{2}-\d{2}"
_AMOUNT = re.compile(r"(?P<label>total borrowings|borrowings|cash, bank balances and current investments|"
                     r"cash and current investments|net debt|reported profit|owners' profit) Rs (?P<v>-?[\d,]+) crore")
_SHARES = re.compile(r"(?P<v>[\d,.]+) crore shares from the filing for (?P<d>" + _DATE + ")")


def _sentences(text: str) -> list[str]:
    return re.split(r"(?<=\.) (?=[A-Z])", text)


def _printed(r: AnalystReport) -> list[tuple[tuple, str]]:
    """((label, view, date), value as printed) for the quantities one analyst prints."""
    out = []
    for s in _sentences(r.finding):
        low = s.lower()
        for m in _AMOUNT.finditer(s):
            label = m.group("label")
            if label in ("reported profit", "owners' profit"):
                d = re.search(r"12 months to (" + _DATE + ")", s)
                d = d.group(1) if d else None
            else:
                after = re.search(_DATE, s[m.end():])
                before = re.findall(_DATE, s[:m.start()])
                d = after.group(0) if after else (before[-1] if before else None)
            view = ""
            if label == "net debt":
                view = ("including leases and gold on loan" if "including leases and gold on loan" in low else
                        "including leases" if "including leases" in low else "before leases")
            if d:
                out.append(((label, view, d), m.group("v")))
        for m in _SHARES.finditer(s):
            out.append((("crore shares", "", m.group("d")), m.group("v")))
    return out


def consistency(reports: list[AnalystReport]) -> list[Check]:
    seen: dict[tuple, dict[str, str]] = {}
    for r in reports:
        for key, v in _printed(r):
            seen.setdefault(key, {}).setdefault(r.analyst_id, v)
    checks = []
    for (label, view, d), by in sorted(seen.items()):
        if len(by) < 2:
            continue
        name = f"{label}{' ' + view if view else ''} on {d}"
        vals = "; ".join(f"{a} {v}" for a, v in by.items())
        if len(set(by.values())) == 1:
            checks.append(Check(f"consistency: {name}", "pass", f"consistency {name}: same in {', '.join(by)} ({vals})"))
        else:
            checks.append(Check(f"consistency: {name}", "fail", f"consistency {name}: different under the same label "
                                                                  f"({vals})"))
    return checks


# ---------------------------------------------------------------- the review step

def set_aside_report(r: AnalystReport, why: str) -> AnalystReport:
    return r.model_copy(update={"views": neutral_views(),
                                "could_not_check": list(r.could_not_check) + [f"set aside by review: {why}"]})


def run_review(state: dict, lake, store) -> Review:
    from smt.desks.lead import lead_report  # desk recombination only; not an analyst helper

    reports = state.get("reports", [])
    iid, as_of = state.get("instrument_id"), state["as_of"]
    rv = Review()
    rv.audit, rv.set_aside = audit(lake, reports, iid, as_of)
    if iid and lake is not None:
        rv.checks = recheck(lake, store, iid, as_of, state.get("stock_type")) + consistency(reports)
    else:
        rv.checks = [Check("re-check", "not checked", "re-check: no lake or instrument id, nothing recomputed")]
        rv.checks += consistency(reports)
    rv.veto = [c.text for c in rv.checks if c.critical and c.status == "fail"]
    desks = []
    for d in state.get("desk_reports", []):
        mine = [r for r in reports if r.unit == d.unit]
        if any(r.analyst_id in rv.set_aside for r in mine):
            fixed = [set_aside_report(r, rv.set_aside[r.analyst_id]) if r.analyst_id in rv.set_aside else r for r in mine]
            desks.append(lead_report(d.unit, d.symbol, d.as_of, fixed, None))
        else:
            desks.append(d)
    rv.desk_reports = desks
    return rv
