"""Valuation stage C step 2: what the current price assumes. Same made-up filings and inputs as
test_cost_of_capital (risk-free 5%, ERP + CRP 6%, beta 1.0 -> cost of equity 9.8% / 11.0% / 12.2%).

Stage A by hand: NOPAT0 = 840 x (1 - 25%) = 630; ROIC 630 / 3,000 = 21.0%; debt Sep 2025 600, minority 200,
cash 400; finance costs 80 / average debt 550. Market value 2,800 -> lead EV 3,200.
"""

import importlib

import pytest
from test_adjustments import CR, GOLD, LEASES, build, feat  # noqa: F401  (build is a fixture)
from test_cost_of_capital import FILINGS, coc, set_beta  # noqa: F401  (coc is a fixture)
from test_valuation import _build

from smt.analysts.reverse_valuation import (G_MAX, NO_G_HIGH, implied_growth, implied_roe, no_growth_value,
                                            reverse_valuation, value_with_growth)
from smt.analysts.valuation import MarketValue

rvmod = importlib.import_module("smt.analysts.reverse_valuation")


def wacc(e, ke, d=600, kd=80 / 550, t=0.25):
    return (e * ke + d * kd * (1 - t)) / (e + d)


def set_mcap(monkeypatch, crore):
    mv = MarketValue(crore * CR, f"Market value Rs {crore:,} crore (made up)")
    monkeypatch.setattr(rvmod, "market_value", lambda ctx, b: mv)


# ---------------------------------------------------------------- pure maths

def test_zero_growth_is_the_no_growth_value():
    assert value_with_growth(100, 0.0, 0.20, 0.10, 10) == pytest.approx(no_growth_value(100, 0.10)) == pytest.approx(1000)
    assert value_with_growth(100, 0.0, 0.20, 0.10, 15) == pytest.approx(1000)


def test_one_year_by_hand():
    # N = 1, g = 10%, R = 20%, WACC 10%: FCF1 = 110 x (1 - 0.5) = 55; TV1 = NOPAT2 / WACC = 121 / 0.1 = 1,210
    # value = (55 + 1,210) / 1.1 = 1,150
    assert value_with_growth(100, 0.10, 0.20, 0.10, 1) == pytest.approx(1150)


def test_solved_growth_prices_back_to_the_ev_within_one_crore():
    ev, nopat = 20_000 * CR, 630 * CR
    s = implied_growth(ev, nopat, 0.21, 0.11, 10)
    assert s.why is None and -0.10 < s.g < G_MAX
    assert abs(value_with_growth(nopat, s.g, 0.21, 0.11, 10) - ev) < 1 * CR
    s15 = implied_growth(ev, nopat, 0.21, 0.11, 15)
    assert s15.g < s.g  # a longer runway needs less growth a year
    assert abs(value_with_growth(nopat, s15.g, 0.21, 0.11, 15) - ev) < 1 * CR


def test_return_not_above_wacc_is_not_solved():
    assert implied_growth(2000, 100, 0.10, 0.10, 10).why == "r_le_w"
    assert implied_growth(2000, 100, 0.08, 0.10, 10).why == "r_le_w"


def test_no_solution_in_range():
    assert implied_growth(1e9, 100, 0.20, 0.10, 10).why == "above"  # needs more than 60% a year
    assert implied_growth(1e9, 100, 0.20, 0.10, 10).g is None
    assert implied_growth(100, 100, 0.20, 0.10, 10).why == "below"  # worth more than the EV even at -10%


def test_lender_formula_round_numbers():
    # P/B 2, cost of equity 11%: g = 0 -> 22%; g = 5% -> 5% + 2 x 6% = 17%
    assert implied_roe(2.0, 0.11, 0.0) == pytest.approx(0.22)
    assert implied_roe(2.0, 0.11, 0.05) == pytest.approx(0.17)
    assert implied_roe(1.5, 0.05, 0.05) is None and implied_roe(1.5, 0.04, 0.05) is None


# ---------------------------------------------------------------- analyst

def test_price_assumes_no_growth_when_share_is_100_or_more(tmp_path, coc):
    ctx = _build(tmp_path)
    f = reverse_valuation(ctx).finding
    w = [wacc(2800, k) for k in (0.098, 0.110, 0.122)]
    share = [630 / x / 3200 for x in w]  # 197% / 179% / 164%
    assert ("enterprise value before leases Rs 3,200 crore (market value + debt Rs 600 crore + minority interest Rs 200 "
            "crore - cash, bank balances and current investments Rs 400 crore on 2025-09-30); NOPAT Rs 630 crore") in f
    assert (f"Today's profit with no growth explains {share[1]:.1%} of the enterprise value ({share[2]:.1%} to "
            f"{share[0]:.1%}). The price assumes no growth, or a fall in profit.") in f
    assert "not a forecast and not an estimate of what the stock is worth" in f
    assert feat(ctx, "val_no_growth_share") == pytest.approx(share[1])
    assert "below -10% / below -10% / below -10%" in f and "even a fall in profit of 10% a year" in f
    assert feat(ctx, "val_implied_growth_10y") is None
    assert all(v.direction.value == "neutral" for v in reverse_valuation(ctx).views)


def test_implied_growth_table_and_features(tmp_path, coc, monkeypatch):
    set_mcap(monkeypatch, 20_000)  # lead EV = 20,000 + 600 + 200 - 400 = 20,400
    ctx = _build(tmp_path)
    f = reverse_valuation(ctx).finding
    w = [wacc(20_000, k) for k in (0.098, 0.110, 0.122)]
    g = [implied_growth(20_400 * CR, 630 * CR, 0.21, x, 10).g for x in w]
    g15 = implied_growth(20_400 * CR, 630 * CR, 0.21, w[1], 15).g
    assert "The rest is growth the price already assumes." in f
    assert (f"return on new capital = ROIC before leases 21.0% (lead): {g[0]:.1%} / {g[1]:.1%} / {g[2]:.1%}") in f
    assert "Lead case for 15 years:" in f and f"{g15:.1%}" in f
    assert "terminal value = next year's NOPAT / WACC" in f
    assert feat(ctx, "val_implied_growth_10y_low") == pytest.approx(g[0])
    assert feat(ctx, "val_implied_growth_10y") == pytest.approx(g[1])
    assert feat(ctx, "val_implied_growth_10y_high") == pytest.approx(g[2])
    assert feat(ctx, "val_implied_growth_15y") == pytest.approx(g15)
    assert abs(value_with_growth(630 * CR, g[1], 0.21, w[1], 10) - 20_400 * CR) < 1 * CR
    # 8 quarters are not loaded in these filings (Sep 2024 to Sep 2025 is 5 quarters)
    assert "year-on-year growth in 12-month revenue and EBIT: not loaded" in f and "Long history and peers come later." in f


def test_no_growth_rate_up_to_60_percent_prints_no_number(tmp_path, coc, monkeypatch):
    set_mcap(monkeypatch, 10_000_000)
    ctx = _build(tmp_path)
    f = reverse_valuation(ctx).finding
    assert "none up to 60% / none up to 60% / none up to 60%" in f and NO_G_HIGH in f
    for k in ("val_implied_growth_10y", "val_implied_growth_10y_low", "val_implied_growth_10y_high",
              "val_implied_growth_15y"):
        assert feat(ctx, k) is None


def test_other_roic_views_are_rows_and_r_below_wacc_is_not_solved(build, coc, monkeypatch):
    set_mcap(monkeypatch, 20_000)
    ctx = build(LEASES + GOLD)
    f = reverse_valuation(ctx).finding
    i_gold = f.index("ROIC including leases and gold on loan 16.6% (lead)")
    assert i_gold < f.index("ROIC including leases 18.8%") < f.index("ROIC before leases 21.0%")
    # a return on new capital below WACC: growth adds no value, nothing solved
    rv = importlib.import_module("smt.analysts.reverse_valuation")
    assert rv._cell(rv.implied_growth(1, 1, 0.05, 0.10, 10)) == "no value from growth"


def test_ebit_not_positive_is_not_computed(tmp_path, coc):
    loss = {k: {"facts": [("ProfitBeforeExceptionalItemsAndTax", -100)]} for k in FILINGS}
    ctx = _build(tmp_path, extra=loss)
    f = reverse_valuation(ctx).finding
    assert "What the price assumes: not computed (12-month EBIT is Rs -360 crore, not positive)." in f  # 4 x (-100 + 20 - 10)
    assert feat(ctx, "val_no_growth_share") is None


def test_missing_wacc_is_not_computed(tmp_path, coc):
    big = {k: {"bs": [("BorrowingsNoncurrent", 2900)]} for k in ("sep24", "sep25")}
    f = reverse_valuation(_build(tmp_path, extra=big)).finding
    assert ("What the price assumes: not computed (no WACC: finance costs / average debt is 2.7%, outside 4% to 15%)."
            in f)


def test_lender_implied_roe(tmp_path, coc, monkeypatch):
    lend = {k: {"facts": [("InterestEarned", 900), ("ImpairmentOnFinancialInstruments", 25)], "bs": [("Loans", 4000)]}
            for k in FILINGS}
    set_mcap(monkeypatch, 5_600)  # owners' equity 2,800 on Sep 2025 -> P/B 2.00
    ctx = _build(tmp_path, extra=lend)
    f = reverse_valuation(ctx).finding
    # g = 0: 2 x cost of equity = 19.6 / 22.0 / 24.4; g = 5%: 2 x cost of equity - 5% = 14.6 / 17.0 / 19.4
    assert "P/B 2.00x" in f
    assert "no growth: 19.6% / 22.0% / 24.4%" in f
    assert "long-run growth assumed equal to the risk-free rate (5.00%): 14.6% / 17.0% / 19.4%" in f
    assert "The price assumes a lasting ROE of 14.6% to 24.4%. The last 12 months' ROE was 21.5%." in f
    assert feat(ctx, "val_implied_roe") == pytest.approx(0.17)
    assert feat(ctx, "val_no_growth_share") is None and feat(ctx, "val_implied_growth_10y") is None
