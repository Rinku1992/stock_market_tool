"""Data office, part 1: freshness clerk and coverage grader. Code only, no LLM.

collect() reads what is loaded for one stock as of a date (the only function that touches the lake). Everything
else is a pure function of that snapshot: the freshness table and the grades.

Due rule for quarterly results (listing rule, not verified against the regulation text): a quarter's results are
due 45 days after the quarter end; the March quarter (filed with the full year) 60 days after.

Grades (fixed rule, same for every stock):
- Price grade: C if no price, or latest close more than 30 days before as_of. B if 8 to 30 days old, or fewer than
  250 daily bars in the last 400 days. Else A.
- Fundamentals grade: C if no filings, fewer than 4 quarters, or the latest loaded quarter is more than one
  quarter behind the latest due quarter. B if 4 to 7 quarters, no verified annual report facts for the latest
  fiscal year, or the latest due quarter is not loaded. Else A (8 or more quarters, latest due quarter loaded,
  verified annual report facts).
- Overall = price grade, capped at B when fundamentals are B or C. Only a price grade of C makes the overall
  grade C (the engine then refuses): a stock with prices but no filings still runs the market activity desk,
  so the daily call log keeps it.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from smt.schema import DataGrade

UTC = timezone.utc
BENCHMARK = "IDX:NIFTY50"
BAR_WINDOW_DAYS = 400
MIN_BARS = 250
PRICE_FRESH_DAYS, PRICE_C_DAYS = 7, 30
DUE_DAYS, DUE_DAYS_MARCH = 45, 60
STALE_PRICE_FOR_TABLE = 7
A, B, C = DataGrade.A, DataGrade.B, DataGrade.C


# ---------------------------------------------------------------- snapshot (reads)

@dataclass
class Snapshot:
    as_of: date
    last_close: date | None = None
    bars_400: int = 0
    index_last: date | None = None
    quarters: list[date] = field(default_factory=list)
    basis: str | None = None
    bs_last: date | None = None
    cf_last: date | None = None
    fact_years: list[date] = field(default_factory=list)  # fiscal year ends (31 March) with verified rows
    verified_facts: int = 0
    coc_version: str | None = None
    coc_missing: str | None = None
    coc_stale: list[str] = field(default_factory=list)


def _bars(lake, iid: str, as_of: date) -> list[date]:
    if lake is None or not lake.has("prices_daily"):
        return []
    cutoff = datetime(as_of.year, as_of.month, as_of.day, 23, 59, 59, tzinfo=UTC)
    rows = lake.query(f"SELECT trade_date FROM {lake.read_sql('prices_daily')} WHERE instrument_id = ? "
                      "AND trade_date <= ? AND published_at <= ? ORDER BY trade_date", [iid, as_of, cutoff])
    return [r[0] for r in rows]


def collect(lake, iid: str | None, as_of: date) -> Snapshot:
    from smt import assumptions
    from smt.data.financials import load_financials

    s = Snapshot(as_of)
    if iid:
        days = _bars(lake, iid, as_of)
        s.last_close = days[-1] if days else None
        s.bars_400 = sum(1 for d in days if (as_of - d).days < BAR_WINDOW_DAYS)
        rows = load_financials(lake, iid, as_of, consolidated=True)
        s.basis = "consolidated"
        if not any(r["line_item"] == "revenue" for r in rows):
            rows, s.basis = load_financials(lake, iid, as_of, consolidated=False), "standalone"
        s.quarters = sorted({r["period_end"] for r in rows if r["period_type"] == "Q" and r["line_item"] == "revenue"})
        if not s.quarters:
            s.basis = None
        bs = [r["period_end"] for r in rows if r["period_type"] == "I" and r["line_item"] in ("equity", "total_assets")]
        cf = [r["period_end"] for r in rows if r["line_item"] == "cfo"]
        s.bs_last, s.cf_last = (max(bs) if bs else None), (max(cf) if cf else None)
        if lake is not None and lake.has("report_facts"):
            from smt.data.financials import market_close_utc
            from smt.sources.owner_review import current_facts
            ok = [r for r in current_facts(lake, iid, market_close_utc(as_of))
                  if r["verified"] is True and r["value"] is not None]
            s.verified_facts = len(ok)
            s.fact_years = sorted({r["fiscal_year_end"] for r in ok
                                   if r["fiscal_year_end"].month == 3 and r["fiscal_year_end"].day == 31})
    idx = _bars(lake, BENCHMARK, as_of)
    s.index_last = idx[-1] if idx else None
    doc = assumptions.load_asof(as_of, assumptions.FOLDER)
    s.coc_version = doc["version"] if doc else None
    inp, why = assumptions.inputs_asof(as_of, assumptions.FOLDER)
    s.coc_missing = why
    s.coc_stale = [k for k, i in (inp or {}).items() if i.stale]
    return s


# ---------------------------------------------------------------- pure rules

def _quarter_ends_back(d: date, n: int) -> list[date]:
    out, y, m = [], d.year, d.month
    for _ in range(n):
        out.append(date(y, m, 31 if m in (3, 12) else 30))
        m -= 3
        if m < 1:
            y, m = y - 1, m + 12
    return out


def due_date(quarter_end: date) -> date:
    return quarter_end + timedelta(days=DUE_DAYS_MARCH if quarter_end.month == 3 else DUE_DAYS)


def latest_due_quarter(as_of: date) -> date:
    """The latest quarter end whose results were due on or before as_of."""
    y = as_of.year
    q = max(e for e in (date(y, 3, 31), date(y, 6, 30), date(y, 9, 30), date(y, 12, 31), date(y - 1, 12, 31))
            if e <= as_of)
    for e in _quarter_ends_back(q, 6):
        if due_date(e) <= as_of:
            return e
    raise ValueError("no due quarter found")  # cannot happen: 6 quarters back is always due


def previous_quarter(q: date) -> date:
    return _quarter_ends_back(q, 2)[1]


def latest_fiscal_year(as_of: date) -> date:
    """Latest 31 March on or before as_of (Indian fiscal year end)."""
    return date(as_of.year if (as_of.month, as_of.day) >= (3, 31) else as_of.year - 1, 3, 31)


@dataclass
class Row:
    dataset: str
    detail: str
    status: str  # fresh, stale, missing
    reason: str


def freshness(s: Snapshot) -> list[Row]:
    d = s.as_of
    rows = []
    if s.last_close is None:
        rows.append(Row("prices", "no daily closes loaded", "missing", "no price on or before the date"))
    else:
        age = (d - s.last_close).days
        rows.append(Row("prices", f"latest close {s.last_close} ({age} days old); {s.bars_400} daily bars in the last "
                                  f"{BAR_WINDOW_DAYS} days", "stale" if age > STALE_PRICE_FOR_TABLE else "fresh",
                        f"more than {STALE_PRICE_FOR_TABLE} days old" if age > STALE_PRICE_FOR_TABLE else "current"))
    if s.index_last is None:
        rows.append(Row("index prices (Nifty 50)", "no closes loaded", "missing", "no index price on or before the date"))
    else:
        age = (d - s.index_last).days
        rows.append(Row("index prices (Nifty 50)", f"latest close {s.index_last} ({age} days old)",
                        "stale" if age > STALE_PRICE_FOR_TABLE else "fresh",
                        f"more than {STALE_PRICE_FOR_TABLE} days old" if age > STALE_PRICE_FOR_TABLE else "current"))
    due = latest_due_quarter(d)
    if not s.quarters:
        rows.append(Row("quarterly results", f"none loaded; latest due quarter {due} (due {due_date(due)})", "missing",
                        "no results filings loaded"))
    else:
        loaded = due in s.quarters
        rows.append(Row("quarterly results", f"latest quarter {s.quarters[-1]}; {len(s.quarters)} quarters; {s.basis}; "
                                             f"latest due quarter {due} (due {due_date(due)}): "
                                             f"{'loaded' if loaded else 'not loaded'}",
                        "fresh" if loaded else "stale", "latest due quarter loaded" if loaded
                        else f"results for {due} were due by {due_date(due)} and are not loaded"))
    half = next(e for e in _quarter_ends_back(due, 4) if e.month in (3, 9))  # balance sheets: Sep and March only
    for name, last in (("balance sheet", s.bs_last), ("cash flow", s.cf_last)):
        if last is None:
            rows.append(Row(name, "none loaded", "missing", "not in the loaded filings"))
        else:
            ok = last >= half
            rows.append(Row(name, f"latest {last}", "fresh" if ok else "stale",
                            f"filed every March and September; latest due {half}" + ("" if ok else ", not loaded")))
    fy = latest_fiscal_year(d)
    if not s.verified_facts:
        rows.append(Row("annual report facts", "no verified rows", "missing", "no verified annual report facts"))
    else:
        last = s.fact_years[-1] if s.fact_years else None
        ok = last == fy
        rows.append(Row("annual report facts", f"latest fiscal year with verified rows {last or 'none'}; "
                                               f"{s.verified_facts} verified rows",
                        "fresh" if ok else "stale", f"latest fiscal year {fy}" + (" covered" if ok else " not covered")))
    if s.coc_missing:
        rows.append(Row("cost of capital inputs", f"version {s.coc_version or 'none'}", "missing", s.coc_missing))
    else:
        rows.append(Row("cost of capital inputs", f"version {s.coc_version}", "stale" if s.coc_stale else "fresh",
                        ("stale: " + ", ".join(s.coc_stale)) if s.coc_stale else "all four inputs current"))
    return rows


@dataclass
class Grades:
    overall: DataGrade
    prices: DataGrade
    fundamentals: DataGrade
    reasons: list[str]
    table: list[Row] = field(default_factory=list)


def price_grade(s: Snapshot) -> tuple[DataGrade, str]:
    if s.last_close is None:
        return C, "prices: grade C, no price loaded"
    age = (s.as_of - s.last_close).days
    if age > PRICE_C_DAYS:
        return C, f"prices: grade C, latest close {s.last_close} is {age} days old (more than {PRICE_C_DAYS})"
    if age > PRICE_FRESH_DAYS:
        return B, f"prices: grade B, latest close {s.last_close} is {age} days old (8 to {PRICE_C_DAYS})"
    if s.bars_400 < MIN_BARS:
        return B, f"prices: grade B, {s.bars_400} daily bars in the last {BAR_WINDOW_DAYS} days (fewer than {MIN_BARS})"
    return A, f"prices: grade A, latest close {s.last_close} ({age} days old), {s.bars_400} bars"


def fundamentals_grade(s: Snapshot) -> tuple[DataGrade, str]:
    if not s.quarters:
        return C, "fundamentals: grade C, no results filings loaded"
    n, due, last = len(s.quarters), latest_due_quarter(s.as_of), s.quarters[-1]
    if n < 4:
        return C, f"fundamentals: grade C, only {n} quarters loaded (fewer than 4)"
    if last < previous_quarter(due):
        return C, (f"fundamentals: grade C, latest loaded quarter {last} is more than one quarter behind the latest "
                   f"due quarter {due}")
    why = []
    if n < 8:
        why.append(f"{n} quarters loaded (4 to 7)")
    fy = latest_fiscal_year(s.as_of)
    if fy not in s.fact_years:
        why.append(f"no verified annual report facts for the fiscal year to {fy}")
    if due not in s.quarters:
        why.append(f"latest due quarter {due} not loaded")
    if why:
        return B, "fundamentals: grade B, " + "; ".join(why)
    return A, f"fundamentals: grade A, {n} quarters, latest due quarter {due} loaded, verified annual report facts"


def grade(s: Snapshot) -> Grades:
    pg, pr = price_grade(s)
    fg, fr = fundamentals_grade(s)
    overall = C if pg == C else (B if pg == A and fg in (B, C) else pg)
    return Grades(overall, pg, fg, [pr, fr], freshness(s))


def office(lake, iid: str | None, as_of: date) -> Grades:
    """The data office for one stock: snapshot, then grades and the freshness table."""
    if lake is None:
        return Grades(B, B, B, ["no data lake: data not graded (test or dry run); grade B by default"], [])
    return grade(collect(lake, iid, as_of))
