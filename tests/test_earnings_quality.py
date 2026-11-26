"""Earnings quality on the made-up filings of test_valuation (every quarter the same):
profit before tax 200 a quarter -> 800 over 12 months; other income 10 -> 40 (5.0%); tax 50 -> 200 (25.0%);
exceptional items 0 (0.0%); revenue 4,000 over 12 months."""

import pytest
from test_adjustments import GOLD, LEASES, build, feat  # noqa: F401  (build is a fixture)
from test_valuation import FILINGS, QUARTER, _build

from smt.analysts.earnings_quality import GOLD_RULE, earnings_quality, faster, load_limits, outside, share, days

WC = {"sep24": {"bs": [("TradeReceivablesCurrent", 300), ("Inventories", 200), ("TradePayablesCurrent", 100)]},
      "sep25": {"bs": [("TradeReceivablesCurrent", 500), ("Inventories", 200), ("TradePayablesCurrent", 300)]}}


def with_quarter(**changes):
    q = dict(QUARTER)
    q.update(changes)
    return {k: list(q.items()) for k in FILINGS}


def test_limits_file():
    st = load_limits()
    assert st["label"] == "starting values, judgement not measured"
    assert st["limits"] == {"exceptional_share": 0.10, "other_income_share": 0.25, "tax_rate_low": 0.18,
                            "tax_rate_high": 0.32, "working_capital_gap": 0.10}


def test_ratios_by_hand(tmp_path):
    ctx = _build(tmp_path, extra=WC)
    r = earnings_quality(ctx)
    f = r.finding
    assert "Exceptional items 0.0% of profit before tax" in f
    assert "Other income 5.0% of profit before tax" in f
    assert "Effective tax rate 25.0% (tax / profit before tax)" in f and "about 25% (context, not verified)" in f
    assert feat(ctx, "eq_tax_rate") == pytest.approx(0.25) and feat(ctx, "eq_other_income_share") == pytest.approx(0.05)
    # receivables average (300 + 500) / 2 = 400 -> 400 / 4,000 x 365 = 36.5 days; inventories 200 -> 18.25;
    # payables (100 + 300) / 2 = 200 -> 18.25; cash cycle 36.5 + 18.25 - 18.25 = 36.5
    assert feat(ctx, "eq_receivable_days") == pytest.approx(36.5)
    assert feat(ctx, "eq_inventory_days") == pytest.approx(18.25) and feat(ctx, "eq_payable_days") == pytest.approx(18.25)
    assert "inventory days 18 (on revenue, not cost of goods)" in f and "average of opening and closing balances" in f
    assert "Against a year earlier: one year only." in f  # Sep 2023 balance sheet and earlier revenue not loaded
    assert "Check:" not in f and all(v.direction.value == "neutral" for v in r.views)
    assert "Not done: capitalised costs, depreciation changes, related-party deals, auditor remarks" in f
    assert "accruals" not in f.lower() and "cash conversion" not in f.lower()


@pytest.mark.parametrize("exc,check", [(20, False), (-20, False), (21, True), (-21, True)])  # 80 / 800 = 10.0%
def test_exceptional_limit_either_way(tmp_path, exc, check):
    f = earnings_quality(_build(tmp_path, quarter=with_quarter(ExceptionalItemsBeforeTax=exc))).finding
    assert ("Check: exceptional items are" in f) is check


@pytest.mark.parametrize("oi,check", [(50, False), (51, True)])  # 200 / 800 = 25.0%; 204 / 800 = 25.5%
def test_other_income_limit(tmp_path, oi, check):
    f = earnings_quality(_build(tmp_path, quarter=with_quarter(OtherIncome=oi))).finding
    assert ("Check: other income is" in f) is check


@pytest.mark.parametrize("tax,check", [(36, False), (35, True), (64, False), (65, True)])  # 18.0%, 17.5%, 32.0%, 32.5%
def test_tax_rate_limits(tmp_path, tax, check):
    f = earnings_quality(_build(tmp_path, quarter=with_quarter(TaxExpense=tax))).finding
    assert ("Check: effective tax rate" in f) is check


def test_check_sentence_with_decimals_reaches_the_page_whole(tmp_path):
    from smt.graph import run
    from smt.report import build_page
    from smt.store import FeatureStore
    from test_valuation import AS_OF, ISIN
    from smt.analysts import DESKS
    ctx = _build(tmp_path, quarter=with_quarter(TaxExpense=35))
    store = FeatureStore()
    state = run("X", AS_OF, store, lake=ctx.lake, instrument_id=ISIN, desks={"equity_research": DESKS["equity_research"]})
    lines = build_page(state, store, "abc").checks["analysts"]
    assert "earnings_quality: Check: effective tax rate 17.5% is outside 18.0% to 32.0%." in lines


def test_working_capital_gap_rule():
    assert not faster(0.20, 0.10, 0.10) and faster(0.2001, 0.10, 0.10) and not faster(0.05, 0.10, 0.10)
    assert outside(0.179, 0.18, 0.32) and not outside(0.18, 0.18, 0.32) and outside(0.321, 0.18, 0.32)
    assert share(10, 0) is None and share(None, 5) is None and days(100, None) is None


def test_jeweller_rule(build):
    ctx = build(LEASES + GOLD, extra=WC)
    f = earnings_quality(ctx).finding
    assert f"payable days {GOLD_RULE}" in f and f"cash cycle {GOLD_RULE}" in f
    assert feat(ctx, "eq_payable_days") is None and feat(ctx, "eq_receivable_days") == pytest.approx(36.5)


def test_lender_gets_items_one_to_three_only(tmp_path):
    lend = {k: {"facts": [("InterestEarned", 900), ("ImpairmentOnFinancialInstruments", 25)], "bs": [("Loans", 4000)]}
            for k in FILINGS}
    ctx = _build(tmp_path, extra=lend)
    f = earnings_quality(ctx).finding
    assert "Effective tax rate 25.0%" in f and "Other income 5.0%" in f
    assert "Working capital" not in f and feat(ctx, "eq_receivable_days") is None


def test_missing_item_is_not_computed_never_zero(tmp_path):
    q = dict(QUARTER)
    del q["TaxExpense"]
    quarter = {k: list(QUARTER) for k in FILINGS}
    quarter["jun25"] = list(q.items())
    ctx = _build(tmp_path, quarter=quarter)
    f = earnings_quality(ctx).finding
    assert "Effective tax rate: not computed (tax or profit before tax not loaded)." in f
    assert feat(ctx, "eq_tax_rate") is None
    assert "receivable days not computed (trade receivables not filed on 2025-09-30)" in f
