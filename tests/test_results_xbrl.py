"""Results XBRL reader and the financial trend analyst.

The fixtures below are small synthetic files that copy the structure of real NSE filings
(checked on Reliance files in Nov 2026), with made-up round numbers.
"""

from datetime import date, datetime, timezone

import pytest

from smt.analysts.base import AnalystContext
from smt.analysts.equity_research import financial_trend
from smt.data.adapter import ingest
from smt.data.financials import load_financials
from smt.data.lake import Lake
from smt.sources.nse_results import IST, NseResultsXbrlFile, ResultsFileError, parse_results_xbrl
from smt.store import FeatureStore

CR = 10_000_000


def _xbrl(*, consolidated=True, isin="INE002A01018", q=("2024-10-01", "2024-12-31"), ytd_dates=None,
          fy="2024-04-01", board=("2025-01-16", "19:15:00"), facts=(), bs=(), cf_ytd=(), ytd=()):
    ns = "http://www.bseindia.com/xbrl/fin/2020-03-31/in-bse-fin"
    ytd_dates = ytd_dates or q  # older format: year-to-date context wrongly carries the quarter's dates

    def ctx(cid, s, e):
        return (f'<xbrli:context id="{cid}"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier></xbrli:entity>'
                f'<xbrli:period><xbrli:startDate>{s}</xbrli:startDate><xbrli:endDate>{e}</xbrli:endDate></xbrli:period></xbrli:context>')

    def num(tag, cid, v, unit="INR"):
        return f'<f:{tag} contextRef="{cid}" unitRef="{unit}" decimals="-7">{v}</f:{tag}>'

    def txt(tag, v):
        return f"<f:{tag} contextRef=\"OneD\">{v}</f:{tag}>"

    body = [ctx("OneD", *q), ctx("FourD", *ytd_dates),
            f'<xbrli:context id="OneI"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier></xbrli:entity>'
            f'<xbrli:period><xbrli:instant>{q[1]}</xbrli:instant></xbrli:period></xbrli:context>',
            txt("Symbol", "RELIANCE"), txt("NatureOfReportStandaloneConsolidated", "Consolidated" if consolidated else "Standalone"),
            txt("DateOfStartOfFinancialYear", fy), txt("DateOfEndOfReportingPeriod", q[1]),
            txt("DateOfEndOfBoardMeeting", board[0]), txt("EndTimeOfBoardMeeting", board[1])]
    if isin:
        body.append(txt("ISIN", isin))
    body += [num(t, "OneD", v * CR) for t, v in facts]
    body += [num(t, "FourD", v * CR) for t, v in ytd]
    body += [num(t, "FourD", v * CR) for t, v in cf_ytd]
    body += [num(t, "OneI", v * CR) for t, v in bs]
    return (f'<?xml version="1.0"?><xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:f="{ns}">'
            + "".join(body) + "</xbrli:xbrl>").encode()


PL = [("RevenueFromOperations", 1000), ("OtherIncome", 20), ("FinanceCosts", 30), ("DepreciationDepletionAndAmortisationExpense", 50),
      ("ProfitBeforeTax", 140), ("ProfitLossForPeriod", 100)]


def test_older_format_year_to_date_gets_the_financial_year_start():
    rf = parse_results_xbrl(_xbrl(facts=PL, ytd=[("RevenueFromOperations", 3000)]))
    rev = sorted((f.start, f.end, f.value) for f in rf.facts if f.tag == "RevenueFromOperations")
    assert rev == [(date(2024, 4, 1), date(2024, 12, 31), 3000 * CR), (date(2024, 10, 1), date(2024, 12, 31), 1000 * CR)]
    assert rf.consolidated and rf.board_end_ist == datetime(2025, 1, 16, 19, 15, tzinfo=IST)


def test_not_a_results_file_fails_loudly():
    with pytest.raises(ResultsFileError):
        parse_results_xbrl(b"<html></html>")
    with pytest.raises(ResultsFileError, match="missing Symbol"):
        parse_results_xbrl(b'<x:xbrl xmlns:x="http://www.xbrl.org/2003/instance"></x:xbrl>')


def test_missing_isin_needs_one_given(tmp_path):
    f = tmp_path / "r.xml"
    f.write_bytes(_xbrl(isin=None, facts=PL))
    with pytest.raises(ResultsFileError, match="--isin"):
        NseResultsXbrlFile(f).parse(f.read_bytes())
    rows = NseResultsXbrlFile(f, isin="INE002A01018").parse(f.read_bytes())
    assert {r["instrument_id"] for r in rows} == {"INE002A01018"}


def test_published_at_is_the_later_of_board_end_and_broadcast(tmp_path):
    f = tmp_path / "r.xml"
    f.write_bytes(_xbrl(facts=PL))
    a = NseResultsXbrlFile(f, broadcast_ist=datetime(2025, 1, 16, 20, 15, 20, tzinfo=IST))
    row = a.parse(f.read_bytes())[0]
    assert row["published_at"] == datetime(2025, 1, 16, 14, 45, 20, tzinfo=timezone.utc)


def _load(lake, tmp_path, name, **kw):
    f = tmp_path / name
    f.write_bytes(_xbrl(**kw))
    ingest(NseResultsXbrlFile(f), lake)


def test_ingest_maps_canonical_items_and_keeps_the_rest(tmp_path):
    lake = Lake(tmp_path / "lake")
    _load(lake, tmp_path, "a.xml", facts=PL + [("SomeRareItem", 7)], bs=[("Equity", 4000)])
    rows = load_financials(lake, "INE002A01018", date(2025, 1, 17))
    items = {(r["statement"], r["period_type"], r["line_item"]) for r in rows}
    assert {("pl", "Q", "revenue"), ("pl", "Q", "net_profit"), ("bs", "I", "equity"), ("pl", "Q", "xbrl:SomeRareItem")} <= items


def test_evening_results_are_not_known_at_that_days_close(tmp_path):
    lake = Lake(tmp_path / "lake")
    _load(lake, tmp_path, "a.xml", facts=PL)  # board meeting ends 16 Jan 2025, 19:15 IST, after market close
    assert load_financials(lake, "INE002A01018", date(2025, 1, 16)) == []
    assert load_financials(lake, "INE002A01018", date(2025, 1, 17))


def test_financial_trend_reports_growth_margins_debt_and_cash(tmp_path):
    lake = Lake(tmp_path / "lake")
    _load(lake, tmp_path, "old.xml", q=("2023-10-01", "2023-12-31"), fy="2023-04-01", board=("2024-01-19", "19:00:00"),
          facts=[("RevenueFromOperations", 800), ("ProfitLossForPeriod", 80)])
    _load(lake, tmp_path, "new.xml", facts=PL,
          bs=[("Equity", 4000), ("BorrowingsNoncurrent", 800), ("BorrowingsCurrent", 200), ("CashAndCashEquivalents", 300)],
          ytd=[("ProfitLossForPeriod", 250)], cf_ytd=[("CashFlowsFromUsedInOperatingActivities", 400),
                                                     ("PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities", 150)])
    ctx = AnalystContext(symbol="RELIANCE", as_of=date(2025, 2, 1), store=FeatureStore(), lake=lake, instrument_id="INE002A01018")
    r = financial_trend(ctx)
    f = r.finding
    assert "revenue Rs 1,000 crore, net profit Rs 100 crore, net margin 10.0%" in f
    assert "EBITDA) Rs 200 crore, margin 20.0%" in f  # 140 + 30 + 50 - 20
    assert "revenue +25.0%, net profit +25.0%" in f
    assert "net debt Rs 700 crore, debt to equity 0.25" in f
    assert "nine months to 2024-12-31: operating cash Rs 400 crore, 1.60 times net profit; capex Rs 150 crore, free cash flow Rs 250 crore" in f
    assert all(v.direction.value == "neutral" for v in r.views) and r.evidence
    assert ctx.store.get_feature_asof("INE002A01018", "fin_debt_to_equity", date(2025, 2, 1))["value"] == pytest.approx(0.25)


def test_financial_trend_without_files():
    ctx = AnalystContext(symbol="X", as_of=date(2025, 2, 1), store=FeatureStore(), instrument_id="INE000000000")
    assert "No quarterly results known" in financial_trend(ctx).finding


def test_standalone_used_only_when_no_consolidated(tmp_path):
    lake = Lake(tmp_path / "lake")
    _load(lake, tmp_path, "s.xml", consolidated=False, facts=PL)
    ctx = AnalystContext(symbol="X", as_of=date(2025, 2, 1), store=FeatureStore(), lake=lake, instrument_id="INE002A01018")
    assert "(standalone)" in financial_trend(ctx).finding


def test_breakdown_contexts_are_skipped():
    x = _xbrl(facts=PL).decode().replace("</xbrli:xbrl>", (
        '<xbrli:context id="I_Other2"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier>'
        '<xbrli:segment><t:Line xmlns:t="t">2</t:Line></xbrli:segment></xbrli:entity>'
        '<xbrli:period><xbrli:instant>2024-12-31</xbrli:instant></xbrli:period></xbrli:context>'
        '<f:RevenueFromOperations contextRef="I_Other2" unitRef="INR" decimals="-7">5</f:RevenueFromOperations></xbrli:xbrl>'))
    rf = parse_results_xbrl(x.encode())
    assert [f.value for f in rf.facts if f.tag == "RevenueFromOperations"] == [1000 * CR]
