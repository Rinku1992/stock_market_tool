"""Valuation stage A on small made-up filings (same structure as real NSE filings, round numbers).

Five filings: Sep 2024 (quarter + half-year cash flow + balance sheet), Dec 2024 (quarter only),
Mar 2025 (quarter + full-year cash flow + balance sheet), Jun 2025 (quarter only),
Sep 2025 (quarter + half-year cash flow + balance sheet). Every quarter has the same P&L, so the
12-month figures are 4 x one quarter and every expected number below can be checked by hand.
"""

from datetime import date, datetime, timedelta, timezone

import pyarrow as pa
import pytest
from test_results_xbrl import CR, _xbrl

from smt.analysts.base import AnalystContext
from smt.analysts.valuation import capital_returns, earnings_power, relative_valuation
from smt.data.adapter import ingest
from smt.data.lake import Lake
from smt.sources.nse_results import NseResultsSegmentsFile, NseResultsXbrlFile
from smt.store import FeatureStore

UTC = timezone.utc
ISIN = "INE002A01018"
AS_OF = date(2025, 11, 28)
PRICE = 280.0

QUARTER = [("RevenueFromOperations", 1000), ("OtherIncome", 10), ("FinanceCosts", 20),
           ("DepreciationDepletionAndAmortisationExpense", 30), ("ProfitBeforeExceptionalItemsAndTax", 200),
           ("ExceptionalItemsBeforeTax", 0), ("ProfitBeforeTax", 200), ("TaxExpense", 50), ("ProfitLossForPeriod", 150),
           ("ProfitOrLossAttributableToOwnersOfParent", 140), ("PaidUpValueOfEquityShareCapital", 100)]
FACE = ("FaceValueOfEquityShareCapital", 10 / CR)  # face value is per share in rupees, not scaled by crore
BS_SEP24 = [("Equity", 2600), ("EquityAttributableToOwnersOfParent", 2400), ("NonControllingInterest", 200),
            ("BorrowingsNoncurrent", 400), ("BorrowingsCurrent", 100), ("CashAndCashEquivalents", 150),
            ("BankBalanceOtherThanCashAndCashEquivalents", 50), ("CurrentInvestments", 100), ("Assets", 4600),
            ("EquityShareCapital", 100)]
BS_SEP25 = [("Equity", 3000), ("EquityAttributableToOwnersOfParent", 2800), ("NonControllingInterest", 200),
            ("BorrowingsNoncurrent", 500), ("BorrowingsCurrent", 100), ("CashAndCashEquivalents", 200),
            ("BankBalanceOtherThanCashAndCashEquivalents", 50), ("CurrentInvestments", 150), ("Assets", 5000),
            ("EquityShareCapital", 100)]
BS_MAR25 = [("Equity", 2800), ("EquityAttributableToOwnersOfParent", 2600), ("Assets", 4800), ("EquityShareCapital", 100)]
CF = "CashFlowsFromUsedInOperatingActivities"
CAPEX = "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"
DIV = "DividendsPaidClassifiedAsFinancingActivities"

# (quarter start, quarter end, fy start, board date, balance sheet, year-to-date cash flow)
FILINGS = {
    "sep24": ("2024-07-01", "2024-09-30", "2024-04-01", "2024-10-25", BS_SEP24, [(CF, 300), (CAPEX, 100), (DIV, 50)]),
    "dec24": ("2024-10-01", "2024-12-31", "2024-04-01", "2025-01-25", [], []),
    "mar25": ("2025-01-01", "2025-03-31", "2024-04-01", "2025-04-25", BS_MAR25, [(CF, 700), (CAPEX, 250), (DIV, 80)]),
    "jun25": ("2025-04-01", "2025-06-30", "2025-04-01", "2025-07-25", [], []),
    "sep25": ("2025-07-01", "2025-09-30", "2025-04-01", "2025-10-25", BS_SEP25, [(CF, 400), (CAPEX, 150), (DIV, 60)]),
}


def _price(lake, close=PRICE):
    rows = []
    for i in range(10):
        d = AS_OF - timedelta(days=i)
        rows.append({"instrument_id": ISIN, "exchange": "NSE", "trade_date": d, "price_basis": "adjusted", "open": close,
                     "high": close, "low": close, "close": close, "volume": 1000, "source": "t", "source_ref": None,
                     "published_at": datetime(d.year, d.month, d.day, 10, tzinfo=UTC),
                     "ingested_at": datetime(2025, 12, 1, tzinfo=UTC), "raw_sha256": "p", "schema_version": 2})
    lake.write("prices_daily", pa.Table.from_pylist(rows), "p")


def _build(tmp_path, skip=(), quarter=None, extra=None, consolidated=True, segments=None):
    """extra: {filing: {"facts": [...], "bs": [...]}} replaces or adds values in that filing."""
    lake = Lake(tmp_path / "lake")
    for name, (qs, qe, fy, board, bs, cf) in FILINGS.items():
        if name in skip:
            continue
        facts = dict(quarter.get(name, QUARTER) if isinstance(quarter, dict) else QUARTER)
        facts[FACE[0]] = FACE[1]
        bsd = dict(bs)
        for k, v in (extra or {}).get(name, {}).get("facts", []):
            facts[k] = v
        for k, v in (extra or {}).get(name, {}).get("bs", []):
            bsd[k] = v
        ytd = [(k, v) for k, v in facts.items() if k == FACE[0]]  # face value also sits in the year-to-date context
        x = _xbrl(consolidated=consolidated, q=(qs, qe), ytd_dates=(fy, qe), fy=fy, board=(board, "18:00:00"),
                  facts=list(facts.items()), bs=list(bsd.items()), cf_ytd=cf, ytd=ytd)
        if segments:
            x = x.decode().replace("</xbrli:xbrl>", (
                '<xbrli:context id="Seg"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier>'
                '<xbrli:segment><t:L xmlns:t="t">1</t:L></xbrli:segment></xbrli:entity><xbrli:period>'
                f'<xbrli:startDate>{qs}</xbrli:startDate><xbrli:endDate>{qe}</xbrli:endDate></xbrli:period></xbrli:context>'
                f'<f:DescriptionOfReportableSegment contextRef="Seg">{segments}</f:DescriptionOfReportableSegment></xbrli:xbrl>')).encode()
        f = tmp_path / f"{name}.xml"
        f.write_bytes(x)
        ingest(NseResultsXbrlFile(f), lake)
        if segments:
            ingest(NseResultsSegmentsFile(f), lake)
    _price(lake)
    return AnalystContext(symbol="X", as_of=AS_OF, store=FeatureStore(), lake=lake, instrument_id=ISIN)


def test_earnings_power(tmp_path):
    r = earnings_power(_build(tmp_path))
    f = r.finding
    assert "12 months to 2025-09-30 (consolidated): reported profit Rs 600 crore, owners' share Rs 560 crore." in f
    assert "Core profit Rs 600 crore (computed by us" in f
    # cash flow to Sep 2025 = half year Sep 2025 400 + year Mar 2025 700 - half year Sep 2024 300 = 800
    assert "operating cash Rs 800 crore, capex Rs 300 crore, free cash flow Rs 500 crore" in f
    assert "Owner earnings Rs 380 crore" in f and "capex is TOTAL capex, not maintenance only" in f  # 560 + 120 - 300
    assert "Cash conversion 1.33" in f  # 800 / 600
    assert "Accruals -4.2% of total assets" in f and "average of 2024-09-30 and 2025-09-30" in f  # (600 - 800) / 4800
    assert all(v.direction.value == "neutral" for v in r.views)


def test_capital_returns_states_the_formula(tmp_path):
    f = capital_returns(_build(tmp_path)).finding
    assert "ROE 21.5%" in f  # 560 / average owners' equity 2600
    # EBIT = 800 + 80 - 40 = 840; tax 25%; invested capital: Sep 2025 3000 + 600 - 400 = 3200, Sep 2024 2600 + 500 - 300 = 2800
    assert "ROIC 21.0%: EBIT Rs 840 crore, tax rate 25%, invested capital Rs 3,000 crore (average of 2024-09-30 and 2025-09-30)" in f
    assert "ROIC = EBIT x (1 - effective tax rate) / (total equity + borrowings - cash, bank balances and current investments)" in f
    assert "Lease liabilities are not in quarterly filings" in f


def test_relative_valuation(tmp_path):
    ctx = _build(tmp_path)
    f = relative_valuation(ctx).finding
    assert "Market value Rs 2,800 crore (10.00 crore shares from the filing for 2025-09-30 x close Rs 280.00 on 2025-11-28)" in f
    assert "Basis: consolidated" in f
    assert "P/E 5.0x on reported owners' profit Rs 560 crore" in f and "earnings yield 20.0%" in f
    assert "P/B 1.0x (owners' equity Rs 2,800 crore on 2025-09-30) next to ROE 21.5%" in f
    assert "Dividend yield 3.2% (dividends paid Rs 90 crore" in f  # 60 + 80 - 50
    # EV = 2800 + 600 borrowings + 200 minority - (200 + 50 + 150) = 3200; EBITDA = 840 + 120 = 960
    assert ("Enterprise value Rs 3,200 crore (market value + borrowings Rs 600 crore + minority interest Rs 200 crore "
            "- cash, bank balances and current investments Rs 400 crore") in f
    assert "EV/EBITDA 3.3x" in f and "EV/Sales 0.8x" in f and "FCF yield 17.9%" in f
    assert ctx.store.get_feature_asof(ISIN, "val_pe", AS_OF)["value"] == pytest.approx(5.0)


def test_only_data_known_at_the_close_is_used(tmp_path):
    ctx = _build(tmp_path)
    early = AnalystContext(symbol="X", as_of=date(2025, 10, 24), store=FeatureStore(), lake=ctx.lake, instrument_id=ISIN)
    assert "12 months to 2025-06-30" in earnings_power(early).finding  # the Sep 2025 results came out on 25 Oct


def test_missing_quarter_is_named_not_zero(tmp_path):
    r = relative_valuation(_build(tmp_path, skip=("dec24",)))
    assert "P/E not computed" in r.finding
    assert any("quarter(s) 2024-12-31 not loaded or not filed" in g for g in r.could_not_check)


def test_negative_profit_means_no_pe(tmp_path):
    loss = [(k, -900 if k in ("ProfitLossForPeriod", "ProfitOrLossAttributableToOwnersOfParent") else v) for k, v in QUARTER]
    f = relative_valuation(_build(tmp_path, quarter={"sep25": loss})).finding
    assert "No P/E: owners' profit over the 12 months to 2025-09-30 is a loss (Rs -480 crore)" in f  # 140 x 3 - 900


def test_share_count_jump_means_split_and_only_later_counts_are_used(tmp_path):
    split = {"facts": [FACE[:1] + (2 / CR,)]}  # face value 10 -> 2: five times the shares
    r = relative_valuation(_build(tmp_path, extra={"jun25": split, "sep25": split}))
    assert any("share count changed +400% between 2025-03-31 and 2025-06-30: likely a split or bonus" in g for g in r.could_not_check)
    assert "Market value Rs 14,000 crore (50.00 crore shares from the filing for 2025-09-30" in r.finding


def test_paid_up_capital_disagreeing_with_balance_sheet(tmp_path):
    bad = {"facts": [("PaidUpValueOfEquityShareCapital", 5)]}
    r = relative_valuation(_build(tmp_path, extra={"sep25": bad, "dec24": bad}))
    g = " ".join(r.could_not_check)
    assert "filing for 2025-09-30: paid-up capital Rs 5 crore disagrees with balance-sheet share capital Rs 100 crore; the balance-sheet figure is used" in g
    assert "filing for 2024-12-31: paid-up capital Rs 5 crore disagrees with share capital Rs 100 crore on the nearest balance sheet" in g
    assert "10.00 crore shares" in r.finding and "split" not in g


def test_zero_equity_is_not_real(tmp_path):
    r = relative_valuation(_build(tmp_path, extra={"sep25": {"bs": [("EquityAttributableToOwnersOfParent", 0)]}}))
    assert "P/B not computed: owners' equity not filed" in r.finding


def test_standalone_used_when_no_consolidated(tmp_path):
    assert "Basis: standalone" in relative_valuation(_build(tmp_path, consolidated=False)).finding


def test_real_estate_leads_with_pb_and_warns_on_pe(tmp_path):
    f = relative_valuation(_build(tmp_path, segments="Real Estate")).finding
    assert f.startswith("Stock type: real estate (router found segment 'Real Estate'")
    assert f.index("P/B 1.0x") < f.index("P/E 5.0x") and "P/E swings with the delivery schedule" in f


def test_real_estate_leads_with_ebit_including_other_income_and_shows_both(tmp_path):
    ctx = _build(tmp_path, segments="Real Estate")
    cr = capital_returns(ctx).finding
    # EBIT with other income = 800 + 80 = 880 -> ROIC 880 x 0.75 / 3000 = 22.0%; without: 840 -> 21.0%
    assert "ROIC 22.0%: EBIT including other income Rs 880 crore" in cr
    assert "Excluding other income: EBIT Rs 840 crore, ROIC 21.0%" in cr
    assert "other income may include interest from joint ventures and project partners (not verified)" in cr
    assert ctx.store.get_feature_asof(ISIN, "val_roic", AS_OF)["value"] == pytest.approx(0.22)
    rv = relative_valuation(ctx).finding
    # EBITDA with other income = 880 + 120 = 1000 -> 3.2x; without: 960 -> 3.3x
    assert "EV/EBITDA including other income 3.2x (EBITDA Rs 1,000 crore)" in rv
    assert "EV/EBITDA excluding other income 3.3x (EBITDA Rs 960 crore)" in rv
    assert rv.index("including other income 3.2x") < rv.index("excluding other income 3.3x")
    assert "project partners (not verified)" in rv
    assert ctx.store.get_feature_asof(ISIN, "val_ev_ebitda", AS_OF)["value"] == pytest.approx(3.2)


def test_general_company_ebit_excludes_other_income_only(tmp_path):
    ctx = _build(tmp_path)
    assert "including other income" not in capital_returns(ctx).finding
    assert "including other income" not in relative_valuation(ctx).finding


def test_lender_skips_ev_roic_and_fcf_and_says_why(tmp_path):
    lend = {k: {"facts": [("InterestEarned", 900), ("ImpairmentOnFinancialInstruments", 25)], "bs": [("Loans", 4000)]}
            for k in FILINGS}
    ctx = _build(tmp_path, extra=lend)
    rv, cr, ep = relative_valuation(ctx).finding, capital_returns(ctx).finding, earnings_power(ctx).finding
    assert rv.index("P/B 1.0x") < rv.index("P/E 5.0x") and "EV, EV/EBITDA, EV/Sales and FCF yield are not used for a lender" in rv
    assert "Enterprise value" not in rv and "FCF yield 1" not in rv
    assert "Return on assets 11.7%" in cr and "ROIC is not used for a lender" in cr  # 560 / 4800
    assert "Credit cost Rs 100 crore over 12 months, 2.50% of loans" in cr
    assert "Owner earnings, free cash flow, cash conversion and accruals are not used for a lender" in ep


def test_no_filings():
    ctx = AnalystContext(symbol="X", as_of=AS_OF, store=FeatureStore(), instrument_id=ISIN)
    for fn in (earnings_power, capital_returns, relative_valuation):
        assert "No quarterly results known" in fn(ctx).finding
