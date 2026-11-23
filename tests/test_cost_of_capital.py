"""Valuation stage C step 1: cost of capital and ROIC against it, on the made-up filings of test_valuation.

Inputs used (percent): G-sec 7.0, India default spread 2.0, mature ERP 4.0, India CRP 2.0.
By hand: risk-free 5.0%; ERP + CRP 6.0%. Raw beta 1.0 -> base 0.67 + 0.33 = 1.00, low 0.80, high 1.20.
Cost of equity 9.8% / 11.0% / 12.2%.
Stage A: EBIT 840, tax 25%, ROIC 630 / 3,000 = 21.0%; borrowings Sep 2025 600, Sep 2024 500 (average 550);
finance costs 4 x 20 = 80; market value 2,800.
"""

import importlib
import json
from datetime import date

import pytest
import test_adjustments as ta
from test_adjustments import GOLD, LEASES, Facts, build, feat  # noqa: F401  (build is a fixture)
from test_valuation import AS_OF, FILINGS, _build

from smt import assumptions
from smt.analysts import cost_of_capital as cc
from smt.analysts.reverse_valuation import reverse_valuation
from smt.analysts.valuation import _base, capital_returns, lead_capital

rvmod = importlib.import_module("smt.analysts.reverse_valuation")
INPUTS = {"gsec_10y": 7.0, "india_default_spread": 2.0, "mature_erp": 4.0, "india_crp": 2.0}
SRC_DATE = date(2025, 11, 1)
ENTERED = date(2025, 11, 20)


@pytest.fixture
def coc(tmp_path, monkeypatch):
    """An assumptions folder with the four inputs entered on ENTERED, and a raw beta of 1.0."""
    folder = tmp_path / "assumptions"
    folder.mkdir()
    (folder / "history").mkdir()
    for k, v in INPUTS.items():
        assumptions.set_input(k, v, f"src {k}", SRC_DATE, ENTERED, folder)
    monkeypatch.setattr(assumptions, "FOLDER", folder)
    set_beta(monkeypatch, 1.0)
    return folder


def set_beta(monkeypatch, raw):
    class Bar:
        d = AS_OF
    out = (None, "no index prices loaded", []) if raw is None else ((raw, 0.5), None, [Bar()])
    monkeypatch.setattr(rvmod, "beta_1y", lambda lake, iid, as_of: out)


def wacc_by_hand(ke, e=2800, d=600, kd=80 / 550, t=0.25):
    return (e * ke + d * kd * (1 - t)) / (e + d)


# ---------------------------------------------------------------- pure maths

def test_risk_free_takes_off_the_default_spread():
    assert cc.risk_free(0.07, 0.02) == pytest.approx(0.05)
    assert cc.risk_free(0.07, 0.02) != pytest.approx(0.07)


def test_cost_of_equity_range_by_hand():
    b = cc.blume_beta(1.0)
    assert (b.low, b.base, b.high) == pytest.approx((0.8, 1.0, 1.2))
    assert cc.cost_of_equity_range(0.05, b, 0.04, 0.02) == pytest.approx((0.098, 0.110, 0.122))


def test_beta_limits():
    assert cc.blume_beta(0.1).base == pytest.approx(0.6) and cc.blume_beta(0.1).clamped  # 0.397 -> 0.6
    assert cc.blume_beta(0.1).low == pytest.approx(0.4)
    assert cc.blume_beta(3.0).base == pytest.approx(1.5) and cc.blume_beta(3.0).high == pytest.approx(1.7)  # 2.34 -> 1.5
    assert cc.blume_beta(1.5).base == pytest.approx(1.335) and not cc.blume_beta(1.5).clamped


def test_cost_of_debt_rules():
    assert cc.cost_of_debt(80, 1000) == (pytest.approx(0.08), None)
    assert cc.cost_of_debt(30, 1000)[1] == "finance costs / average debt is 3.0%, outside 4% to 15%"
    assert cc.cost_of_debt(200, 1000)[1] == "finance costs / average debt is 20.0%, outside 4% to 15%"
    assert cc.cost_of_debt(80, 0)[1] == "debt is zero"
    assert cc.cost_of_debt(80, None)[1] == "debt is missing"
    assert cc.cost_of_debt(None, 1000)[1] == "12-month finance costs are not loaded"


def test_wacc_and_spread_by_hand():
    w = cc.wacc_range(2800, 600, (0.098, 0.110, 0.122), 80 / 550, 0.25)
    assert w == pytest.approx(tuple(wacc_by_hand(k) for k in (0.098, 0.110, 0.122)))
    assert w[1] == pytest.approx(0.109840, abs=1e-6)
    assert cc.spread_range(0.21, w[0], w[2]) == pytest.approx((0.21 - w[2], 0.21 - w[0]))


# ---------------------------------------------------------------- assumptions file

def test_shipped_empty_version_is_kept_and_current_file_has_all_inputs():
    empty = json.loads((assumptions.FOLDER / "history" / "cost_of_capital_empty.json").read_text())
    assert empty["entered_on"] is None and all(x["value"] is None for x in empty["inputs"].values())
    doc = json.loads((assumptions.FOLDER / "cost_of_capital.json").read_text())
    assert set(doc["inputs"]) == set(assumptions.INPUTS)
    assert all(x["value"] is None or (x["source"] and x["source_date"]) for x in doc["inputs"].values())


def test_each_set_is_a_new_version_and_old_ones_are_kept(tmp_path):
    folder = tmp_path / "a"
    folder.mkdir()
    assumptions.set_input("gsec_10y", 7.0, "s1", SRC_DATE, ENTERED, folder)
    assumptions.set_input("gsec_10y", 7.2, "s2", SRC_DATE, ENTERED, folder)
    assumptions.set_input("gsec_10y", 7.4, "s3", SRC_DATE, date(2025, 11, 25), folder)
    hist = sorted(p.name for p in (folder / "history").glob("*.json"))
    assert hist == ["cost_of_capital_2025-11-20.2.json", "cost_of_capital_2025-11-20.json"]
    assert assumptions.load_asof(date(2025, 11, 24), folder)["inputs"]["gsec_10y"]["value"] == 7.2  # latest that day
    assert assumptions.load_asof(date(2025, 11, 25), folder)["inputs"]["gsec_10y"]["value"] == 7.4
    assert assumptions.load_asof(date(2025, 11, 19), folder) is None


def test_set_refuses_a_fraction_and_a_future_source_date(tmp_path):
    with pytest.raises(ValueError, match="enter percent"):
        assumptions.set_input("gsec_10y", 0.07, "s", SRC_DATE, ENTERED, tmp_path)
    with pytest.raises(ValueError, match="after today"):
        assumptions.set_input("gsec_10y", 7.0, "s", date(2025, 12, 1), ENTERED, tmp_path)


# ---------------------------------------------------------------- analyst

def test_reverse_valuation_by_hand(tmp_path, coc):
    ctx = _build(tmp_path)
    capital_returns(ctx)
    f = reverse_valuation(ctx).finding
    assert "Cost of equity 9.8% to 12.2% (base 11.0%) = risk-free 5.00% + beta x (mature market equity risk premium " \
           "4.00% + India country risk premium 2.00%)" in f
    assert ("Risk-free 5.00% = India 10-year government bond yield 7.00% (src gsec_10y, 2025-11-01) minus India default "
            "spread (Damodaran, same table) 2.00% (src india_default_spread, 2025-11-01): the bond yield already holds") in f
    assert "taken off once, not counted twice" in f
    assert "Beta 1.00 (range 0.80 to 1.20) = 0.67 x raw 1-year beta to Nifty 50 1.00" in f and cc.BETA_LABEL in f
    assert ("Cost of debt 14.5% before tax = 12-month finance costs Rs 80 crore to 2025-09-30 / average debt Rs 550 crore "
            "(average of 2024-09-30 and 2025-09-30; debt before leases") in f
    assert "Weights at market value: equity Rs 2,800 crore (82%" in f and "debt Rs 600 crore on 2025-09-30 (18%)" in f
    assert "WACC 10.0% to 12.0% (base 11.0%), tax rate 25% (the rate ROIC uses)." in f
    # spread: 21.0 - 11.97 = +9.0; 21.0 - 10.0 = +11.0; base 21.0 - 10.98 = +10.0
    assert "ROIC 21.0% (before leases) against cost of capital 10.0% to 12.0%: spread +9.0 to +11.0 points (base +10.0)." in f
    assert f.endswith(cc.GROWTH_LINE)
    order = [f.index(s) for s in ("Cost of equity", "Cost of debt", "Weights", "WACC", "ROIC 21.0%", "Growth adds")]
    assert order == sorted(order)
    ke = (0.098, 0.110, 0.122)
    assert feat(ctx, "val_cost_of_equity") == pytest.approx(0.110)
    assert feat(ctx, "val_cost_of_debt") == pytest.approx(80 / 550)
    assert feat(ctx, "val_wacc_low") == pytest.approx(wacc_by_hand(ke[0]))
    assert feat(ctx, "val_wacc") == pytest.approx(wacc_by_hand(ke[1]))
    assert feat(ctx, "val_wacc_high") == pytest.approx(wacc_by_hand(ke[2]))
    assert feat(ctx, "val_roic_spread") == pytest.approx(630 / 3000 - wacc_by_hand(ke[1]))
    assert all(v.direction.value == "neutral" for v in reverse_valuation(ctx).views)


def test_lead_roic_matches_capital_returns(build, coc, monkeypatch):
    for sub, rows, name, debt in (("p", (), "val_roic", [600, 500]),
                                  ("l", LEASES, "val_roic_incl_leases", [1000, 800]),
                                  ("g", LEASES + GOLD, "val_roic_incl_leases_gold", [1500, 1200])):
        ctx = build(rows, sub=sub)
        capital_returns(ctx)
        lead, _ = lead_capital(ctx, _base(ctx))
        assert lead.roic == pytest.approx(feat(ctx, name)), sub
        assert [x / ta.CR for x in lead.debt] == pytest.approx(debt), sub


def test_debt_follows_the_lead_view_with_leases_and_gold(build, coc):
    ctx = build(LEASES + GOLD)
    f = reverse_valuation(ctx).finding
    # debt = borrowings + leases + gold: Sep 2025 600 + 400 + 500 = 1,500; Sep 2024 500 + 300 + 400 = 1,200; average 1,350
    assert "average debt Rs 1,350 crore" in f and "debt including leases and gold on loan" in f
    assert feat(ctx, "val_cost_of_debt") == pytest.approx(80 / 1350)
    w = (2800 * 0.110 + 1500 * (80 / 1350) * 0.75) / 4300
    assert feat(ctx, "val_wacc") == pytest.approx(w)
    assert feat(ctx, "val_roic_spread") == pytest.approx(630 / 3800 - w)


def test_missing_input_means_no_cost_of_capital(tmp_path, monkeypatch):
    folder = tmp_path / "a"
    folder.mkdir()
    for k in ("gsec_10y", "mature_erp", "india_crp"):
        assumptions.set_input(k, INPUTS[k], "s", SRC_DATE, ENTERED, folder)
    monkeypatch.setattr(assumptions, "FOLDER", folder)
    set_beta(monkeypatch, 1.0)
    ctx = _build(tmp_path)
    r = reverse_valuation(ctx)
    assert "Cost of capital not computed: cost of capital inputs not entered: india_default_spread" in r.finding
    assert feat(ctx, "val_cost_of_equity") is None


def test_version_entered_after_as_of_is_not_used(tmp_path, monkeypatch):
    folder = tmp_path / "a"
    folder.mkdir()
    for k, v in INPUTS.items():
        assumptions.set_input(k, v, "s", SRC_DATE, date(2025, 11, 29), folder)  # the day after AS_OF
    monkeypatch.setattr(assumptions, "FOLDER", folder)
    set_beta(monkeypatch, 1.0)
    f = reverse_valuation(_build(tmp_path)).finding
    assert "Cost of capital not computed: no cost of capital inputs entered on or before 2025-11-28" in f


def test_stale_inputs_are_flagged(tmp_path, monkeypatch):
    folder = tmp_path / "a"
    folder.mkdir()
    old = {"gsec_10y": date(2025, 10, 13), "mature_erp": date(2024, 10, 23)}  # 46 and 401 days before AS_OF
    for k, v in INPUTS.items():
        assumptions.set_input(k, v, "s", old.get(k, SRC_DATE), ENTERED, folder)
    monkeypatch.setattr(assumptions, "FOLDER", folder)
    set_beta(monkeypatch, 1.0)
    r = reverse_valuation(_build(tmp_path))
    assert "7.00% (s, 2025-10-13; stale: older than 45 days)" in r.finding
    assert "4.00% (s, 2024-10-23; stale: older than 400 days)" in r.finding
    assert "2.00% (s, 2025-11-01)" in r.finding  # not stale
    assert sum("(stale)" in g for g in r.could_not_check) == 2
    assumptions.set_input("gsec_10y", 7.0, "s", date(2025, 10, 14), date(2025, 11, 21), folder)  # 45 days: not stale
    (tmp_path / "b").mkdir()
    assert "stale: older than 45 days" not in reverse_valuation(_build(tmp_path / "b")).finding


def test_beta_limit_in_text(tmp_path, coc, monkeypatch):
    set_beta(monkeypatch, 3.0)
    ctx = _build(tmp_path)
    f = reverse_valuation(ctx).finding
    assert "Beta 1.50 (range 1.30 to 1.70)" in f and "(limit applied)" in f
    assert feat(ctx, "val_cost_of_equity") == pytest.approx(0.05 + 1.5 * 0.06)


def test_no_beta_means_no_cost_of_capital(tmp_path, coc, monkeypatch):
    set_beta(monkeypatch, None)
    ctx = _build(tmp_path)
    assert "Cost of capital not computed: no beta (no index prices loaded)." in reverse_valuation(ctx).finding
    assert feat(ctx, "val_cost_of_equity") is None


def test_real_beta_function_without_index_prices(tmp_path, coc, monkeypatch):
    monkeypatch.setattr(rvmod, "beta_1y", __import__("smt.analysts.market_activity", fromlist=["x"]).beta_1y)
    assert "no beta (no index prices loaded)" in reverse_valuation(_build(tmp_path)).finding


def test_cost_of_debt_out_of_range_shows_cost_of_equity_only(tmp_path, coc):
    big = {k: {"bs": [("BorrowingsNoncurrent", 2900)]} for k in ("sep24", "sep25")}  # debt 3,000 both: 80 / 3,000 = 2.7%
    ctx = _build(tmp_path, extra=big)
    f = reverse_valuation(ctx).finding
    assert "Cost of debt not used: finance costs / average debt is 2.7%, outside 4% to 15%" in f
    assert "Cost of equity only: no WACC and no spread." in f and "WACC" not in f.split("no WACC")[1]
    assert feat(ctx, "val_cost_of_equity") == pytest.approx(0.110)
    assert feat(ctx, "val_wacc") is None and feat(ctx, "val_roic_spread") is None


def test_zero_debt(tmp_path, coc):
    zero = {k: {"bs": [("BorrowingsNoncurrent", 0), ("BorrowingsCurrent", 0)]} for k in ("sep24", "sep25")}
    ctx = _build(tmp_path, extra=zero)
    assert "Cost of debt not used: debt is zero" in reverse_valuation(ctx).finding
    assert feat(ctx, "val_wacc") is None


def test_lender_roe_against_cost_of_equity(tmp_path, coc):
    lend = {k: {"facts": [("InterestEarned", 900), ("ImpairmentOnFinancialInstruments", 25)], "bs": [("Loans", 4000)]}
            for k in FILINGS}
    ctx = _build(tmp_path, extra=lend)
    f = reverse_valuation(ctx).finding
    assert "No WACC for a lender" in f and "Cost of debt" not in f
    # ROE = 560 / average owners' equity (2,800 + 2,400) / 2 = 2,600 -> 21.5%; spread 21.54 - 12.2 / - 9.8
    assert "ROE 21.5% (12 months to 2025-09-30) against cost of equity 9.8% to 12.2%: spread +9.3 to +11.7 points " \
           "(base +10.5)." in f
    assert feat(ctx, "val_roe_spread") == pytest.approx(560 / 2600 - 0.110)
    assert feat(ctx, "val_wacc") is None and feat(ctx, "val_roic_spread") is None
