"""Valuation stage B step 2: leases and R&D from verified annual report facts, on the made-up filings of
test_valuation (every quarter the same; balance sheets Sep 2024 and Sep 2025). A June 2024 quarter is
added so FY25 (Apr 2024 - Mar 2025) has 4 quarters of finance costs: 4 x 20 = Rs 80 crore.

Stage A by hand: EBIT 840, tax 25%, NOPAT 630; invested capital Sep 2025 3,200, Sep 2024 2,800 (average
3,000); EV 3,200; operating EBITDA 960; revenue 4,000.
"""

import json
from datetime import date, datetime, timezone

import pytest
import test_valuation as tv
from test_valuation import AS_OF, CR, ISIN, _build

from smt.analysts import adjustments as adj
from smt.analysts.valuation import capital_returns, relative_valuation
from smt.data.adapter import ingest

UTC = timezone.utc
FY25, FY24, FY23 = date(2025, 3, 31), date(2024, 3, 31), date(2023, 3, 31)
FILED = datetime(2025, 6, 1, 12, tzinfo=UTC)


class Facts:
    """Test adapter: report_facts rows through the contract."""
    name, dataset, raw_ext, context = "test_facts", "report_facts", "json", {}

    def __init__(self, rows):
        self.rows = rows

    def fetch(self):
        return json.dumps(self.rows, default=str).encode()

    def parse(self, payload):
        out = []
        for r in json.loads(payload):
            out.append({**r, "fiscal_year_end": date.fromisoformat(r["fiscal_year_end"]),
                        "published_at": datetime.fromisoformat(r["published_at"])})
        return out


def f(item, fy, crore, verified=True, published=FILED):
    return {"instrument_id": ISIN, "fiscal_year_end": fy, "item": item, "value": crore * CR, "unit": "crore", "page": 1,
            "quote": "q", "extractor": "test", "verified": verified, "check_note": None, "published_at": published}


LEASES = [f("lease_liabilities_current", FY25, 100), f("lease_liabilities_noncurrent", FY25, 300),
          f("lease_liabilities_current", FY24, 80), f("lease_liabilities_noncurrent", FY24, 220),
          f("interest_on_lease_liabilities", FY25, 30)]


@pytest.fixture
def build(tmp_path, monkeypatch):
    monkeypatch.setitem(tv.FILINGS, "jun24", ("2024-04-01", "2024-06-30", "2024-04-01", "2024-07-25", [], []))

    def make(rows=(), sub="a", **kw):
        (tmp_path / sub).mkdir(exist_ok=True)
        ctx = _build(tmp_path / sub, **kw)
        if rows:
            ingest(Facts(list(rows)), ctx.lake)
        return ctx
    return make


def feat(ctx, name):
    got = ctx.store.get_feature_asof(ISIN, name, AS_OF)
    return None if got is None else got["value"]


def test_ev_and_roic_including_leases(build):
    ctx = build(LEASES)
    cr = capital_returns(ctx)
    # leases: Sep 2025 uses FY25 (100 + 300 = 400), Sep 2024 uses FY24 (80 + 220 = 300)
    # invested capital including leases = ((3,200 + 400) + (2,800 + 300)) / 2 = 3,350; ROIC = 630 / 3,350 = 18.8%
    assert cr.finding.index("ROIC including leases 18.8%") < cr.finding.index("Before leases: ROIC 21.0%")
    assert ("invested capital including leases Rs 3,350 crore (average of 2024-09-30 and 2025-09-30; lease liabilities "
            "Rs 400 crore as of 2025-03-31 and Rs 300 crore as of 2024-03-31, from the annual report)") in cr.finding
    assert "Lease liabilities are not in quarterly filings" not in cr.finding
    assert "lease interest is inside finance costs" in cr.finding
    assert feat(ctx, "val_roic_incl_leases") == pytest.approx(630 / 3350)
    assert feat(ctx, "val_roic") == pytest.approx(0.21)  # stage A unchanged
    rv = relative_valuation(ctx)
    # EV including leases = 3,200 + 400 = 3,600; EV/EBITDA 3,600 / 960 = 3.75; EV/Sales 3,600 / 4,000 = 0.9
    assert rv.finding.index("Enterprise value including leases Rs 3,600 crore") < rv.finding.index("Before leases: Enterprise value Rs 3,200 crore")
    assert "(enterprise value + lease liabilities Rs 400 crore as of 2025-03-31, from the annual report)" in rv.finding
    assert "EV/EBITDA including leases 3.8x (same EBITDA Rs 960 crore)" in rv.finding and "EV/Sales including leases 0.9x" in rv.finding
    assert "left out" not in rv.finding
    assert feat(ctx, "val_ev_incl_leases") == pytest.approx(3600 * CR)
    assert feat(ctx, "val_ev_ebitda_incl_leases") == pytest.approx(3.75)
    assert feat(ctx, "val_ev_sales_incl_leases") == pytest.approx(0.9)
    assert feat(ctx, "val_lease_liabilities") == pytest.approx(400 * CR)
    assert feat(ctx, "val_ev") == pytest.approx(3200 * CR) and feat(ctx, "val_ev_ebitda") == pytest.approx(3200 / 960)
    assert feat(ctx, "val_ev_sales") == pytest.approx(0.8)


def _not_adjusted(ctx, reason):
    cr, rv = capital_returns(ctx), relative_valuation(ctx)
    assert "ROIC 21.0%: EBIT Rs 840 crore" in cr.finding and "including leases" not in cr.finding
    assert "Lease liabilities are not in quarterly filings" in cr.finding
    assert "Enterprise value Rs 3,200 crore" in rv.finding and "including leases" not in rv.finding
    assert any(reason in g and "ROIC is before leases" in g for g in cr.could_not_check), cr.could_not_check
    assert any(reason in g and "EV is before leases" in g for g in rv.could_not_check), rv.could_not_check
    for name in ("val_roic_incl_leases", "val_ev_incl_leases", "val_lease_liabilities"):
        assert feat(ctx, name) is None
    assert feat(ctx, "val_roic") == pytest.approx(0.21) and feat(ctx, "val_ev") == pytest.approx(3200 * CR)


def test_missing_or_unverified_lease_row_means_no_adjustment(build):
    missing = [r for r in LEASES if not (r["item"] == "lease_liabilities_current" and r["fiscal_year_end"] == FY25)]
    _not_adjusted(build(missing, sub="m"), "lease liabilities for 2025-03-31: current and non-current are not both verified")
    unverified = [f("lease_liabilities_current", FY25, 100, verified=False)] + LEASES[1:]
    _not_adjusted(build(unverified, sub="u"), "lease liabilities for 2025-03-31: current and non-current are not both verified")
    _not_adjusted(build(sub="n"), "no lease liabilities from an annual report")


def test_lease_figure_older_than_400_days_is_not_used(build):
    old = [f("lease_liabilities_current", FY24, 80), f("lease_liabilities_noncurrent", FY24, 220),
           f("interest_on_lease_liabilities", FY24, 30)]
    _not_adjusted(build(old), "latest lease liabilities are for 2024-03-31, more than 400 days before 2025-09-30")


def test_lease_interest_larger_than_finance_costs_is_flagged(build):
    rows = LEASES[:4] + [f("interest_on_lease_liabilities", FY25, 100)]
    _not_adjusted(build(rows), "interest on lease liabilities for 2025-03-31 (Rs 100 crore) is larger than finance costs "
                               "for the 4 quarters to 2025-03-31 (Rs 80 crore)")
    _not_adjusted(build(LEASES[:4], sub="b"), "interest on lease liabilities for 2025-03-31 is not verified")


def test_fact_published_after_as_of_is_not_used(build):
    after_close = datetime(2025, 11, 28, 10, 1, tzinfo=UTC)  # 15:31 IST on the as-of day
    _not_adjusted(build([{**r, "published_at": after_close} for r in LEASES]), "no lease liabilities from an annual report")


def test_rd_capitalisation_by_hand():
    rd = adj.rd_capitalise({FY25: 421 * CR, FY24: 420 * CR})
    assert rd.asset == pytest.approx(841 * CR) and rd.amortisation == pytest.approx(420 * CR)
    assert rd.ebit_change == pytest.approx(1 * CR) and rd.known == 2 and rd.earliest == FY24
    wide = adj.rd_capitalise({FY25: 2900 * CR, FY24: 2630 * CR})
    assert wide.asset == pytest.approx(5530 * CR) and wide.amortisation == pytest.approx(2630 * CR)
    assert wide.ebit_change == pytest.approx(270 * CR)
    # three known years: asset = 30 + 2/3 x 24 + 1/3 x 18 = 52; amortisation = (24 + 18 + 18) / 3 = 20
    three = adj.rd_capitalise({FY25: 30, FY24: 24, FY23: 18})
    assert three.asset == pytest.approx(52) and three.amortisation == pytest.approx(20) and three.earliest == FY23
    assert adj.rd_capitalise({FY25: 30}) is None
    assert adj.rd_capitalise({FY25: 30, FY23: 18}) is None  # the year before R0 is needed


RD = [f("rnd_expenditure", FY25, 421), f("rnd_expenditure", FY24, 420),
      f("rd_and_innovation_total", FY25, 2900), f("rd_and_innovation_total", FY24, 2630)]
CAVEAT = "only 2 years of R&D figures; earlier years assumed equal to FY24; latest fiscal year R&D set against 12-month EBIT"


def test_rough_roic_with_rd_capitalised_narrow_and_wide(build):
    ctx = build(LEASES + RD)
    cr = capital_returns(ctx).finding
    # narrow: NOPAT 630 + 1 = 631; invested capital 3,350 + 841 = 4,191; ROIC 15.1%
    assert ("Rough ROIC with R&D capitalised 15.1% (narrow: R&D only): research asset Rs 841 crore (R0 + 2/3 x R1 + 1/3 x R2, "
            "3-year life, R0 = 2025-03-31), amortisation Rs 420 crore, EBIT change +Rs 1 crore, adjusted NOPAT Rs 631 crore, "
            f"adjusted invested capital Rs 4,191 crore; rough: {CAVEAT}.") in cr
    # wide: NOPAT 630 + 270 = 900; invested capital 3,350 + 5,530 = 8,880; ROIC 10.1%
    assert ("Rough ROIC with R&D capitalised 10.1% (wide: R&D and innovation; includes innovation spend, which is not R&D "
            "by the accounting definition): research asset Rs 5,530 crore") in cr
    assert "EBIT change +Rs 270 crore" in cr and cr.index("15.1% (narrow") < cr.index("10.1% (wide")
    assert all("rough" in s for s in cr.split("Rough ROIC")[1:])
    assert feat(ctx, "val_rd_asset_rough") == pytest.approx(841 * CR)
    assert feat(ctx, "val_roic_rd_rough") == pytest.approx(631 / 4191)
    assert feat(ctx, "val_roic_rd_wide_rough") == pytest.approx(900 / 8880)
    assert feat(ctx, "val_roic") == pytest.approx(0.21)


def test_rd_needs_two_years_consolidated_figures_and_leases(build):
    one = build(LEASES + [f("rnd_expenditure", FY25, 421)], sub="one")
    r = capital_returns(one)
    assert "Rough ROIC" not in r.finding
    assert any("rnd_expenditure: only FY25 is verified; at least 2 consecutive years are needed" in g for g in r.could_not_check)
    standalone_only = build(LEASES + [f("rd_expense_standalone", FY25, 32), f("rd_expense_standalone", FY24, 26)], sub="sa")
    r = capital_returns(standalone_only)
    assert "Rough ROIC" not in r.finding and feat(standalone_only, "val_rd_asset_rough") is None
    assert [g for g in r.could_not_check if g.startswith("R&D")] == [
        "R&D: no verified consolidated R&D figures in the annual report facts; not capitalised"]
    basis = build(LEASES + RD, sub="basis", consolidated=False)
    r = capital_returns(basis)
    assert "Rough ROIC" not in r.finding
    assert "R&D: valuation basis is standalone; R&D is capitalised only on a consolidated basis" in r.could_not_check
    no_leases = build(RD, sub="nl")
    r = capital_returns(no_leases)
    assert "Rough ROIC" not in r.finding
    assert any("invested capital including leases is not available" in g for g in r.could_not_check)


def test_lender_report_unchanged(build):
    lend = {k: {"facts": [("InterestEarned", 900), ("ImpairmentOnFinancialInstruments", 25)], "bs": [("Loans", 4000)]}
            for k in list(tv.FILINGS)}
    plain, with_facts = build(sub="p", extra=lend), build(LEASES + RD, sub="w", extra=lend)
    for fn in (capital_returns, relative_valuation):
        a, b = fn(plain), fn(with_facts)
        assert a.finding == b.finding and a.could_not_check == b.could_not_check
        assert "including leases" not in b.finding and "Rough ROIC" not in b.finding


def test_fiscal_year_finance_costs_fall_back_to_the_full_year_figure():
    from smt.analysts.valuation import Fin, fy_finance_costs
    q = lambda e, v: {"period_end": e, "period_type": "Q", "line_item": "finance_costs", "value": v}
    quarters = [q(date(2025, 6, 30), 1), q(date(2025, 9, 30), 2), q(date(2025, 12, 31), 3), q(date(2026, 3, 31), 4)]
    fy = date(2026, 3, 31)
    assert fy_finance_costs(Fin(quarters, "consolidated"), fy) == (10, "4 quarters added up")
    annual = {"period_end": fy, "period_type": "A", "line_item": "finance_costs", "value": 10}
    v, how = fy_finance_costs(Fin(quarters[1:] + [annual], "consolidated"), fy)
    assert v == 10 and how == "full-year figure filed for 2026-03-31 (quarter 2025-06-30 not loaded)"
    assert fy_finance_costs(Fin(quarters[1:], "consolidated"), fy)[0] is None


def test_no_finance_costs_for_the_lease_year_means_no_adjustment(tmp_path):
    (tmp_path / "x").mkdir()
    ctx = _build(tmp_path / "x")  # no June 2024 quarter and no full-year figure: FY25 finance costs unknown
    ingest(Facts(LEASES), ctx.lake)
    _not_adjusted(ctx, "finance costs for the fiscal year to 2025-03-31 are not loaded, so the lease check cannot be made")
