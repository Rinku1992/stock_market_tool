"""Stock type router: lender or general, decided per stock from filing tags."""

from datetime import date

from smt.data.lake import Lake
from smt.graph import run
from smt.router import classify, stock_type_router
from smt.analysts.base import AnalystContext
from smt.store import FeatureStore


def _row(end, item, value, pt="Q"):
    return {"period_end": end, "period_type": pt, "line_item": item, "value": value}


GENERAL = [_row(date(2024, 12, 31), "revenue", 100), _row(date(2024, 12, 31), "equity", 50, "I")]


def test_no_filings_is_unknown_not_a_guess():
    assert classify([]).label == "unknown"


def test_general_company():
    st = classify(GENERAL)
    assert st.label == "general" and st.triggers == []


def test_loans_make_a_lender_and_the_trigger_is_shown():
    st = classify(GENERAL + [_row(date(2024, 12, 31), "loans", 900, "I")])
    assert st.label == "lender" and st.triggers == ["Loans in 1 period(s), latest 2024-12-31"]
    assert "router found Loans" in st.describe()


def test_quarter_without_balance_sheet_still_routes():
    # June and December filings have no balance sheet, so no Loans tag
    st = classify([_row(date(2024, 6, 30), "interest_income", 80), _row(date(2024, 6, 30), "credit_cost", 10)])
    assert st.label == "lender" and "InterestEarned and ImpairmentOnFinancialInstruments" in st.triggers[0]


def test_one_lender_filing_makes_the_stock_a_lender():
    rows = GENERAL + [_row(date(2023, 12, 31), "revenue", 90), _row(date(2023, 12, 31), "loans", 500, "I")]
    assert classify(rows).label == "lender"


def test_weak_signals_do_not_make_a_lender():
    d = date(2024, 12, 31)
    assert classify(GENERAL + [_row(d, "loans", 0, "I")]).label == "general"  # zero may be an unfilled tag
    assert classify(GENERAL + [_row(d, "interest_income", 5)]).label == "general"  # interest alone is not lending
    assert classify(GENERAL + [_row(d, "interest_income", 0), _row(d, "credit_cost", 0)]).label == "general"


def test_router_needs_no_llm_and_reads_the_lake(tmp_path):
    ctx = AnalystContext(symbol="X", as_of=date(2025, 2, 1), store=FeatureStore(), lake=Lake(tmp_path), instrument_id="INE0")
    assert stock_type_router(ctx) == "unknown"


def test_graph_uses_the_router_by_default():
    state = run("X", date(2025, 2, 1), FeatureStore(), desks={"equity_research": []})
    assert state["verdict"].stock_type == "unknown"  # no lake, no filings: no guess
