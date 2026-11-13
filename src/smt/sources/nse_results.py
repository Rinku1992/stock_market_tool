"""Quarterly results files (XBRL) that the owner downloads BY HAND from NSE.

Handles both formats seen so far (checked on Reliance files, Nov 2026):
- older "Financial Results" filings (BSE taxonomy, up to quarter ended Dec 2024): P&L only.
- newer "Integrated Filing - Financials" (SEBI taxonomy): P&L, balance sheet and cash flow.

Values in these files are already absolute rupees (the "decimals" attribute only says they
were rounded to crores), which matches our data contract.

Trap in the older format: year-to-date contexts (ids starting "Four") carry the QUARTER's dates
although the values are year-to-date. We set their start to the financial year start.

Only plain (non-segment) facts are stored. Of the segment facts, only the segment NAMES are kept
(filing_segments dataset), for the stock type router. Segment values are skipped.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc


class ResultsFileError(ValueError):
    pass


# Our canonical names for the line items analysts use. Everything else is stored as "xbrl:<Tag>".
CANONICAL = {
    # P&L
    "RevenueFromOperations": ("pl", "revenue"),
    "OtherIncome": ("pl", "other_income"),
    "Income": ("pl", "total_income"),
    "Expenses": ("pl", "total_expenses"),
    "EmployeeBenefitExpense": ("pl", "employee_cost"),
    "FinanceCosts": ("pl", "finance_costs"),
    "InterestEarned": ("pl", "interest_income"),  # lenders
    "ImpairmentOnFinancialInstruments": ("pl", "credit_cost"),  # lenders
    "DepreciationDepletionAndAmortisationExpense": ("pl", "depreciation"),
    "ProfitBeforeExceptionalItemsAndTax": ("pl", "pbt_before_exceptional"),
    "ExceptionalItemsBeforeTax": ("pl", "exceptional_items"),
    "ProfitBeforeTax": ("pl", "pbt"),
    "TaxExpense": ("pl", "tax"),
    "ProfitLossForPeriod": ("pl", "net_profit"),
    "ProfitOrLossAttributableToOwnersOfParent": ("pl", "net_profit_owners"),
    "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations": ("pl", "eps_basic"),
    "DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations": ("pl", "eps_diluted"),
    "PaidUpValueOfEquityShareCapital": ("pl", "paid_up_capital"),
    "FaceValueOfEquityShareCapital": ("pl", "face_value"),
    # Balance sheet
    "Assets": ("bs", "total_assets"),
    "CurrentAssets": ("bs", "current_assets"),
    "CurrentLiabilities": ("bs", "current_liabilities"),
    "Inventories": ("bs", "inventories"),
    "TradeReceivablesCurrent": ("bs", "trade_receivables"),
    "TradePayablesCurrent": ("bs", "trade_payables"),
    "CashAndCashEquivalents": ("bs", "cash"),
    "CurrentInvestments": ("bs", "current_investments"),
    "PropertyPlantAndEquipment": ("bs", "ppe"),
    "CapitalWorkInProgress": ("bs", "cwip"),
    "Equity": ("bs", "equity"),
    "EquityShareCapital": ("bs", "share_capital"),
    "BankBalanceOtherThanCashAndCashEquivalents": ("bs", "other_bank_balances"),
    "EquityAttributableToOwnersOfParent": ("bs", "equity_owners"),
    "NonControllingInterest": ("bs", "minority_interest"),
    "BorrowingsNoncurrent": ("bs", "borrowings_noncurrent"),
    "BorrowingsCurrent": ("bs", "borrowings_current"),
    # Lenders (NBFC results taxonomy, checked on M&MFIN files, Nov 2026)
    "Loans": ("bs", "loans"),
    "Borrowings": ("bs", "borrowings_other_than_debt_securities"),
    "DebtSecurities": ("bs", "debt_securities"),
    "Deposits": ("bs", "deposits"),
    "SubordinatedLiabilities": ("bs", "subordinated_liabilities"),
    # Cash flow
    "CashFlowsFromUsedInOperatingActivities": ("cf", "cfo"),
    "CashFlowsFromUsedInInvestingActivities": ("cf", "cfi"),
    "CashFlowsFromUsedInFinancingActivities": ("cf", "cff"),
    "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities": ("cf", "capex"),
    "PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities": ("cf", "capex_intangibles"),
    "DividendsPaidClassifiedAsFinancingActivities": ("cf", "dividends_paid"),
    "InterestPaidClassifiedAsFinancingActivities": ("cf", "interest_paid"),
}
_CF_HINTS = ("CashFlow", "ClassifiedAs", "AdjustmentsFor", "Proceeds", "Payments", "Repayments")


@dataclass
class Fact:
    tag: str
    start: date | None  # None for a balance-sheet (instant) value
    end: date
    value: float


@dataclass
class ResultsFile:
    symbol: str
    isin: str | None
    consolidated: bool
    fy_start: date
    period_end: date
    board_end_ist: datetime | None
    facts: list[Fact] = field(default_factory=list)
    segments: list[str] = field(default_factory=list)  # names of reportable segments


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_results_xbrl(payload: bytes) -> ResultsFile:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as e:
        raise ResultsFileError(f"not a valid XML file: {e}") from e
    if _local(root.tag) != "xbrl":
        raise ResultsFileError("not an XBRL file")

    contexts: dict[str, tuple[date | None, date, bool]] = {}  # id -> (start, end, has_dimensions)
    for e in root:
        if _local(e.tag) != "context":
            continue
        per = e.find(".//{*}period")
        s, en, inst = per.find("{*}startDate"), per.find("{*}endDate"), per.find("{*}instant")
        # a breakdown (segment, or a split of "other" lines) has a segment or scenario part: skip those facts
        dims = any(_local(m.tag) in ("segment", "scenario", "explicitMember", "typedMember") for m in e.iter())
        start = date.fromisoformat(s.text.strip()) if s is not None else None
        end = date.fromisoformat((en if en is not None else inst).text.strip())
        contexts[e.get("id")] = (start, end, dims)

    text: dict[str, str] = {}
    raw: list[tuple[str, str, str]] = []  # (tag, context id, text)
    segments: set[str] = set()
    for e in root:
        c = e.get("contextRef")
        if _local(e.tag) == "DescriptionOfReportableSegment" and (e.text or "").strip():
            segments.add(" ".join(e.text.split()))  # a name: kept even though its context is a breakdown
        if not c or c not in contexts or contexts[c][2]:
            continue
        tag, val = _local(e.tag), (e.text or "").strip()
        if e.get("unitRef"):
            raw.append((tag, c, val))
        else:
            text.setdefault(tag, val)

    for need in ("Symbol", "NatureOfReportStandaloneConsolidated", "DateOfStartOfFinancialYear", "DateOfEndOfReportingPeriod"):
        if need not in text:
            raise ResultsFileError(f"missing {need}: this does not look like an NSE results file")
    fy_start = date.fromisoformat(text["DateOfStartOfFinancialYear"])
    board_end = None
    if text.get("DateOfEndOfBoardMeeting") and text.get("EndTimeOfBoardMeeting"):
        board_end = datetime.fromisoformat(f"{text['DateOfEndOfBoardMeeting']}T{text['EndTimeOfBoardMeeting']}").replace(tzinfo=IST)

    facts: dict[tuple[str, date | None, date], float] = {}
    for tag, c, val in raw:
        start, end, _ = contexts[c]
        if start is not None and c.startswith("Four") and start != fy_start:
            start = fy_start  # older format: year-to-date context carries the quarter's dates
        try:
            v = float(val)
        except ValueError:
            continue
        key = (tag, start, end)
        if key in facts and facts[key] != v:
            raise ResultsFileError(f"{tag} has two different values for the same period {start} to {end}")
        facts[key] = v
    return ResultsFile(
        symbol=text["Symbol"].strip().upper(), isin=(text.get("ISIN") or "").strip().upper() or None,
        consolidated=text["NatureOfReportStandaloneConsolidated"].strip().lower() == "consolidated",
        fy_start=fy_start, period_end=date.fromisoformat(text["DateOfEndOfReportingPeriod"]),
        board_end_ist=board_end, facts=[Fact(t, s, e, v) for (t, s, e), v in facts.items()],
        segments=sorted(segments),
    )


def period_type(start: date | None, end: date) -> str:
    if start is None:
        return "I"
    days = (end - start).days + 1
    return "Q" if days <= 100 else "H" if days <= 200 else "N" if days <= 290 else "A"


def statement_and_item(tag: str, instant: bool) -> tuple[str, str]:
    if tag in CANONICAL:
        return CANONICAL[tag]
    if instant:
        return "bs", f"xbrl:{tag}"
    return ("cf" if any(h in tag for h in _CF_HINTS) else "pl"), f"xbrl:{tag}"


class NseResultsXbrlFile:
    """A results XBRL file saved by hand, stored in the financials dataset."""

    name = "nse_manual_file"
    dataset = "financials"
    raw_ext = "xml"

    def __init__(self, path, isin: str | None = None, broadcast_ist: datetime | None = None) -> None:
        self.path = path
        self.isin = isin.upper() if isin else None
        self.broadcast_ist = broadcast_ist

    @property
    def context(self) -> dict:
        return {"file": str(self.path).rsplit("/", 1)[-1], "isin": self.isin,
                "broadcast_ist": self.broadcast_ist.isoformat() if self.broadcast_ist else None}

    def fetch(self) -> bytes:
        with open(self.path, "rb") as f:
            return f.read()

    def published_at(self, rf: ResultsFile) -> datetime:
        """When the market could first see these numbers: NSE broadcast time if given, else board meeting end."""
        times = [t for t in (self.broadcast_ist, rf.board_end_ist) if t is not None]
        if not times:
            raise ResultsFileError("no board meeting time in the file; pass the NSE broadcast time")
        return max(times).astimezone(UTC)

    def parse(self, payload: bytes) -> list[dict]:
        rf = parse_results_xbrl(payload)
        isin = rf.isin or self.isin
        if not isin:
            raise ResultsFileError(f"the file has no ISIN for {rf.symbol}; pass it with --isin")
        if rf.isin and self.isin and rf.isin != self.isin:
            raise ResultsFileError(f"ISIN in the file ({rf.isin}) differs from the one given ({self.isin})")
        published = self.published_at(rf)
        rows, seen = [], set()
        for f in rf.facts:
            statement, item = statement_and_item(f.tag, f.start is None)
            row = {"instrument_id": isin, "period_end": f.end, "period_type": period_type(f.start, f.end),
                   "statement": statement, "consolidated": rf.consolidated, "line_item": item, "value": f.value,
                   "published_at": published, "source_ref": f.tag}
            key = (row["period_end"], row["period_type"], statement, item)
            if key not in seen:  # a quarter-one year-to-date equals the quarter: keep one
                seen.add(key)
                rows.append(row)
        return rows


class NseResultsSegmentsFile(NseResultsXbrlFile):
    """The same results file, read for its segment names only (filing_segments dataset)."""

    dataset = "filing_segments"

    def parse(self, payload: bytes) -> list[dict]:
        rf = parse_results_xbrl(payload)
        isin = rf.isin or self.isin
        if not isin:
            raise ResultsFileError(f"the file has no ISIN for {rf.symbol}; pass it with --isin")
        published = self.published_at(rf)
        return [{"instrument_id": isin, "period_end": rf.period_end, "consolidated": rf.consolidated,
                 "segment_name": name, "published_at": published, "source_ref": None} for name in rf.segments]
