"""Data office part 1: freshness clerk and coverage grader. Pure rules on made-up snapshots, then made-up lakes."""

from datetime import date, timedelta

import pytest
from test_valuation import AS_OF, ISIN, _price

from smt.analysts.market_activity import liquidity, price_structure
from smt.data.lake import Lake
from smt.data_office import (Snapshot, due_date, fundamentals_grade, grade, latest_due_quarter, office, price_grade,
                             previous_quarter)
from smt.graph import run
from smt.report import build_page, to_markdown
from smt.review import audit
from smt.schema import AnalystKind, AnalystReport, Call, DataGrade, Horizon, HorizonView, Direction
from smt.scoring import score
from smt.store import FeatureStore

D = date(2026, 11, 24)  # latest due quarter: 2026-09-30 (due 2026-11-14)
A, B, C = DataGrade.A, DataGrade.B, DataGrade.C


def quarters(n, last=date(2026, 9, 30)):
    out, q = [], last
    for _ in range(n):
        out.append(q)
        q = previous_quarter(q)
    return sorted(out)


def snap(age=0, bars=300, n=8, last=date(2026, 9, 30), facts=(date(2026, 3, 31),)):
    return Snapshot(D, last_close=None if age is None else D - timedelta(days=age), bars_400=bars,
                    quarters=quarters(n, last) if n else [], basis="consolidated" if n else None,
                    fact_years=list(facts), verified_facts=len(facts))


# ---------------------------------------------------------------- due rule

def test_due_rule_june_and_march_quarters():
    assert due_date(date(2026, 6, 30)) == date(2026, 8, 14)  # + 45 days
    assert due_date(date(2026, 3, 31)) == date(2026, 5, 30)  # + 60 days for March
    assert latest_due_quarter(date(2026, 8, 14)) == date(2026, 6, 30)
    assert latest_due_quarter(date(2026, 8, 13)) == date(2026, 3, 31)
    assert latest_due_quarter(date(2026, 5, 30)) == date(2026, 3, 31)
    assert latest_due_quarter(date(2026, 5, 29)) == date(2025, 12, 31)
    assert latest_due_quarter(D) == date(2026, 9, 30)


# ---------------------------------------------------------------- price grade

@pytest.mark.parametrize("age,bars,expected", [(0, 300, A), (7, 300, A), (8, 300, B), (30, 300, B), (31, 300, C),
                                               (0, 250, A), (0, 249, B), (None, 0, C)])
def test_price_grade_edges(age, bars, expected):
    assert price_grade(snap(age=age, bars=bars))[0] == expected


# ---------------------------------------------------------------- fundamentals grade

@pytest.mark.parametrize("n,last,facts,expected", [
    (0, date(2026, 9, 30), (date(2026, 3, 31),), C),
    (3, date(2026, 9, 30), (date(2026, 3, 31),), C),
    (4, date(2026, 9, 30), (date(2026, 3, 31),), B),
    (7, date(2026, 9, 30), (date(2026, 3, 31),), B),
    (8, date(2026, 9, 30), (date(2026, 3, 31),), A),
    (8, date(2026, 9, 30), (), B),  # no verified annual report facts
    (8, date(2026, 9, 30), (date(2025, 3, 31),), B),  # facts only for an older fiscal year
    (8, date(2026, 6, 30), (date(2026, 3, 31),), B),  # latest due quarter not loaded (one quarter behind)
    (8, date(2026, 3, 31), (date(2026, 3, 31),), C),  # more than one quarter behind
])
def test_fundamentals_grade_edges(n, last, facts, expected):
    assert fundamentals_grade(snap(n=n, last=last, facts=facts))[0] == expected


def test_overall_is_price_grade_capped_by_fundamentals():
    assert grade(snap()).overall == A
    assert grade(snap(n=5)).overall == B  # prices A, fundamentals B
    assert grade(snap(n=0)).overall == B  # prices A, fundamentals C: still runs
    assert grade(snap(age=8)).overall == B
    assert grade(snap(age=31)).overall == C  # only the price grade refuses
    g = grade(snap(n=0))
    assert g.reasons == ["prices: grade A, latest close 2026-11-24 (0 days old), 300 bars",
                         "fundamentals: grade C, no results filings loaded"]


def test_freshness_table_rows():
    rows = {r.dataset: r for r in grade(snap(n=8, last=date(2026, 6, 30), age=10)).table}
    assert rows["prices"].status == "stale" and "10 days old" in rows["prices"].detail
    assert rows["quarterly results"].status == "stale"
    assert "results for 2026-09-30 were due by 2026-11-14 and are not loaded" == rows["quarterly results"].reason
    assert rows["index prices (Nifty 50)"].status == "missing"
    assert rows["annual report facts"].status == "fresh"
    assert set(rows) == {"prices", "index prices (Nifty 50)", "quarterly results", "balance sheet", "cash flow",
                         "annual report facts", "cost of capital inputs"}


# ---------------------------------------------------------------- made-up lakes

def test_prices_but_no_filings_still_runs(tmp_path):
    lake = Lake(tmp_path / "lake")
    _price(lake)  # 10 daily closes up to AS_OF
    store = FeatureStore()
    state = run("X", AS_OF, store, lake=lake, instrument_id=ISIN, desks={"market_activity": [price_structure, liquidity]})
    v = state["verdict"]
    assert v.refused_reason is None and len(v.horizons) == 3 and len(state["reports"]) == 2
    assert (v.data_grade, v.price_grade, v.fundamentals_grade) == (B, B, C)
    md = to_markdown(build_page(state, store, "abc"))
    assert "- **Data grade:** B (prices B, fundamentals C)" in md
    assert "  - fundamentals: grade C, no results filings loaded" in md
    assert "## 7. Data" in md and "| quarterly results | none loaded" in md


def test_no_prices_is_refused(tmp_path):
    state = run("X", AS_OF, FeatureStore(), lake=Lake(tmp_path / "lake"), instrument_id=ISIN)
    v = state["verdict"]
    assert v.data_grade == C and v.horizons == [] and state["reports"] == []
    assert "prices: grade C, no price loaded" in v.refused_reason


def test_desk_whose_data_is_grade_c_blocks_the_call():
    g = grade(snap(n=0))  # fundamentals C
    views = [HorizonView(horizon=h, direction=Direction.POSITIVE, strength=1.0, confidence=1.0) for h in Horizon]
    from smt.schema import DeskReport
    desk = DeskReport(unit="equity_research", symbol="X", as_of=D, views=views, facts=["x"], weights_version="t",
                      summary="s", summary_source="template")
    w = {"version": "t", "weights": {"equity_research": {h.value: 1.0 for h in Horizon}},
         "thresholds": {"min_score": 0.1, "min_confidence": 0.3}}
    v = score("X", D, B, "general", [desk], ["equity_research"], None, weights=w, grades=g)
    assert all(h.call == Call.NO_CALL for h in v.horizons)
    assert ("desk equity_research (weight 1) needs fundamentals data, which has grade C (fundamentals: grade C, no "
            "results filings loaded)") in v.horizons[0].reasons
    assert v.fundamentals_grade == C and v.grade_reasons == g.reasons


def test_not_computed_report_is_not_a_fail_but_an_unsourced_figure_is(tmp_path):
    neutral = [HorizonView(horizon=h, direction=Direction.NEUTRAL, strength=0.0, confidence=0.0) for h in Horizon]
    mk = lambda aid, f: AnalystReport(analyst_id=aid, unit="u", kind=AnalystKind.CODE, symbol="X", as_of=D, finding=f,
                                      views=neutral)
    checks, aside = audit(Lake(tmp_path / "lake"), [mk("beta", "Beta not computed: no index prices loaded."),
                                                    mk("mom", "Momentum not computed: needs about 13 months of prices."),
                                                    mk("rev", "Revenue Rs 5 crore.")], ISIN, D)
    by = {c.name: c for c in checks}
    assert by["evidence: beta"].status == "not computed" and by["evidence: mom"].status == "not computed"
    assert by["evidence: beta"].text == "beta: Beta not computed: no index prices loaded."
    assert by["evidence: rev"].status == "fail" and set(aside) == {"rev"}


def test_no_lake_keeps_grade_b_and_says_so():
    g = office(None, None, D)
    assert g.overall == B and "no data lake" in g.reasons[0]
