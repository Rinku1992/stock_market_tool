"""Annual report extraction: the LLM reads, code checks. Made-up page text and FakeLLM."""

import json
from decimal import Decimal
from datetime import date, datetime, timezone

import pytest

from smt.data.lake import Lake
from smt.data.adapter import ingest, reparse
from smt.data.validate import conform
from smt.llm import FakeLLM
from smt.sources.annual_report import (NEEDS_REVIEW_LEASE, NEEDS_REVIEW_NUMBERS, ReportFactsRecord, check_fact, extract,
                                       fact_rows, is_consolidated_page, plan_calls, published_at_for, quote_numbers)
from smt.store import FeatureStore

FY = date(2026, 3, 31)
YEARS = [FY, date(2025, 3, 31)]
ISIN = "INE000TEST01"

STANDALONE = "Notes to standalone financial statements\n(` in crore)\nInterest on lease liabilities 99 98\nLease liabilities 500 400"
LEASE_NOTE = ("Notes to consolidated financial statements\nfor the year ended 31st March 2026\n` in crore\n"
              "Particulars Year ended 31 March 2026 Year ended 31 March 2025\n"
              "Interest on lease liabilities 1,234.50 1,100\n"
              "Depreciation on right-of-use assets 2,345 2,010\n"
              "Lease liabilities - current 3,456 3,000\nLease liabilities - non-current 12,345 11,000")
ESOP_NOTE = "Notes to consolidated financial statements\nRs in lakh\nShare based payments (expense) (77) (65)"
BOARD = "Board's report\nExpenditure on R&D Standalone Consolidated FY 2026 FY 2025 FY 2026 FY 2025\nTotal R&D expenditure 418 415 421 420\n(H crore)"
PAGES = ["Cover page", STANDALONE, LEASE_NOTE, ESOP_NOTE, BOARD]  # PDF pages 1..5


def fact(**kw):
    base = {"item": "interest_on_lease_liabilities", "fiscal_year_end": "2026-03-31", "value": 1234.5, "unit": "crore",
            "page": 3, "quote": "Interest on lease liabilities 1,234.50"}
    return {**base, **kw}


SENT = {3: LEASE_NOTE}


def test_good_extraction_passes_and_is_converted_to_rupees():
    clean, why = check_fact(fact(), SENT, YEARS)
    assert why is None and clean["value"] == pytest.approx(1234.5 * 1e7) and clean["unit"] == "crore"
    # line breaks in the PDF text do not matter; the words must still be the same
    clean, why = check_fact(fact(quote="Interest on lease\nliabilities  1,234.50"), SENT, YEARS)
    assert why is None


def test_quote_not_on_the_page_is_rejected():
    _, why = check_fact(fact(quote="Interest on lease liabilities 1,234.50 1,200"), SENT, YEARS)
    assert why == "quote is not on page 3 word for word"
    _, why = check_fact(fact(page=2), {2: STANDALONE, 3: LEASE_NOTE}, YEARS)
    assert why == "quote is not on page 2 word for word"


def test_number_not_matching_the_quote_is_rejected():
    _, why = check_fact(fact(value=1243.5), SENT, YEARS)
    assert why == "value 1243.5 is not a number in the quote"
    # a converted value (rupees instead of the printed crore figure) does not match either
    _, why = check_fact(fact(value=12345000000), SENT, YEARS)
    assert "is not a number in the quote" in why


def test_missing_unit_is_rejected():
    _, why = check_fact(fact(unit=""), SENT, YEARS)
    assert why == "no unit stated"
    _, why = check_fact(fact(unit="thousand"), SENT, YEARS)
    assert why == "unit 'thousand' is not crore, lakh or million"
    # a unit the page does not state is rejected too
    _, why = check_fact(fact(unit="million"), SENT, YEARS)
    assert why == "page 3 does not state the unit 'million'"


def test_other_checks():
    assert check_fact(fact(page=9), SENT, YEARS)[1].startswith("page 9 was not one of the pages sent")
    assert check_fact(fact(fiscal_year_end="2024-03-31"), SENT, YEARS)[1] == "fiscal year end 2024-03-31 was not asked for"
    assert check_fact(fact(item="goodwill"), SENT, YEARS)[1] == "unknown item 'goodwill'"
    # a number printed in brackets is matched without them; lakh converts by 1e5
    clean, why = check_fact(fact(item="share_based_payment_expense", value=77, unit="lakh", page=4,
                                 quote="Share based payments (expense) (77)"), {4: ESOP_NOTE}, YEARS)
    assert why is None and clean["value"] == pytest.approx(77e5)
    assert quote_numbers("Total 1,23,456.7 (89)") == [Decimal("123456.7"), Decimal(89)]
    # a value given as printed text ("1,234.50", "(77)") is read the same way
    assert check_fact(fact(value="1,234.50"), SENT, YEARS)[0]["value"] == pytest.approx(1234.5e7)
    assert check_fact(fact(item="share_based_payment_expense", value="(77)", unit="lakh", page=4,
                           quote="Share based payments (expense) (77)"), {4: ESOP_NOTE}, YEARS)[1] is None


def test_candidate_pages_and_calls():
    assert is_consolidated_page(LEASE_NOTE) and not is_consolidated_page(STANDALONE)
    calls, missing = plan_calls(PAGES)
    assert missing == []
    asked = {tuple(c.items): c.pages for c in calls}
    # the four lease items share the consolidated lease note (not the standalone page); ESOP and R&D have their own pages
    lease = ("depreciation_right_of_use", "interest_on_lease_liabilities", "lease_liabilities_current", "lease_liabilities_noncurrent")
    assert any(set(lease) <= set(items) for items in asked)
    assert all(2 not in pages for pages in asked.values())  # standalone page never sent
    assert all(len(p) <= 3 for p in asked.values())
    assert plan_calls(["Cover", STANDALONE])[1] == ["lease_liabilities_current", "lease_liabilities_noncurrent",
                                                     "interest_on_lease_liabilities", "depreciation_right_of_use",
                                                     "share_based_payment_expense", "rnd_expenditure"]


def _answer(facts):
    return "```json\n" + json.dumps({"facts": facts, "not_found": []}) + "\n```"


def test_end_to_end_with_fake_llm(tmp_path):
    good = fact()
    wrong = fact(item="depreciation_right_of_use", value=2354, quote="Depreciation on right-of-use assets 2,345 2,010")
    fy25 = fact(fiscal_year_end="2025-03-31", value=1100, quote="Interest on lease liabilities 1,234.50 1,100")
    llm = FakeLLM(FeatureStore(), {"report_facts": _answer([good, wrong, fy25])})
    ex = extract(llm, tmp_path / "none.pdf", "TEST", ISIN, FY, date(2026, 6, 1), pages=["Cover", "Blank", LEASE_NOTE])
    assert llm.sent == 1 and len(ex.calls) == 1
    rows = {(r["item"], r["fiscal_year_end"]): r for r in fact_rows(ex)}
    assert rows[("interest_on_lease_liabilities", FY)]["verified"] is True
    fy25_row = rows[("interest_on_lease_liabilities", date(2025, 3, 31))]
    assert fy25_row["value"] == pytest.approx(1100e7) and fy25_row["verified"] is False
    assert fy25_row["check_note"] == NEEDS_REVIEW_NUMBERS
    # asked but not answered, and never asked (no page): "not found" rows, value null, never zero
    assert rows[("lease_liabilities_current", FY)]["check_note"] == "not found on the pages sent"
    assert rows[("rnd_expenditure", FY)]["value"] is None and rows[("rnd_expenditure", FY)]["check_note"].startswith("not found")
    bad = rows[("depreciation_right_of_use", FY)]
    assert bad["verified"] is False and bad["value"] is None and "not a number in the quote" in bad["check_note"]
    # stored through the contract, and the checks can be re-run from the raw file
    lake = Lake(tmp_path / "lake")
    ingest(ReportFactsRecord(ex), lake)
    got = lake.query(f"SELECT item, verified, epoch(published_at) FROM {lake.read_sql('report_facts')} "
                     "WHERE item IN ('depreciation_right_of_use', 'interest_on_lease_liabilities') ORDER BY item, fiscal_year_end")
    assert [(i, v) for i, v, _ in got] == [("depreciation_right_of_use", False), ("interest_on_lease_liabilities", False),
                                           ("interest_on_lease_liabilities", True)]
    assert lake.query(f"SELECT count(*) FROM {lake.read_sql('report_facts')}")[0][0] == len(rows)
    assert datetime.fromtimestamp(got[0][2], timezone.utc) == datetime(2026, 6, 1, 18, 30, tzinfo=timezone.utc)
    raw = next((tmp_path / "lake").rglob("*.json.gz"))
    reparse(ReportFactsRecord(ex), lake, raw)


def test_bad_json_answer_is_stored_as_not_verified(tmp_path):
    llm = FakeLLM(FeatureStore(), {"report_facts": "Sorry, I cannot"})
    ex = extract(llm, tmp_path / "none.pdf", "TEST", ISIN, FY, date(2026, 6, 1), pages=["Cover", ESOP_NOTE])
    (row,) = [r for r in fact_rows(ex) if r["item"] == "share_based_payment_expense"]
    assert row["verified"] is False and "not valid JSON" in row["check_note"]


def test_two_different_verified_values_are_both_rejected():
    a = fact(item="lease_liabilities_current", value=3456, quote="Lease liabilities - current 3,456")
    b = fact(item="lease_liabilities_current", value=12345, quote="Lease liabilities - non-current 12,345")
    from smt.sources.annual_report import Extraction
    ex = Extraction("T", ISIN, "x.pdf", "abc", FY, date(2026, 6, 1), "fake",
                    calls=[{"items": ["lease_liabilities_current"], "pages": {"3": LEASE_NOTE}, "response": _answer([a, b])}])
    (row,) = fact_rows(ex)
    assert row["verified"] is False and row["check_note"] == "two different values were given for this item and year"


def test_filing_date_is_needed_to_store():
    from smt.sources.annual_report import Extraction
    with pytest.raises(ValueError, match="never guessed"):
        ReportFactsRecord(Extraction("T", ISIN, "x.pdf", "abc", FY, None, "fake"))
    assert published_at_for(date(2026, 6, 1)) == datetime(2026, 6, 1, 18, 30, tzinfo=timezone.utc)


def test_dry_run_makes_no_call(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SMT_LLM_DRY_RUN", "1")
    llm = FakeLLM(FeatureStore(), {"report_facts": "{}"})
    ex = extract(llm, tmp_path / "none.pdf", "TEST", ISIN, FY, None, pages=PAGES)
    assert llm.sent == 0 and len(ex.calls) >= 1 and llm.dry_run_tokens > 0
    assert "[dry run] task=report_facts" in capsys.readouterr().out


def test_balance_sheet_is_sent_for_current_and_noncurrent_lease_liabilities():
    bs = "Consolidated Balance Sheet\n(H crore)\nNon-current liabilities\nLease liabilities 9,000 8,000\nCurrent liabilities\nLease liabilities 2,000 1,500"
    calls, _ = plan_calls(["Cover", LEASE_NOTE, "Consolidated Statement of Cash Flows\nRepayment of lease liabilities (1,861)", bs])
    lease_call = next(c for c in calls if "lease_liabilities_current" in c.items)
    assert 4 in lease_call.pages and 2 in lease_call.pages and len(lease_call.pages) <= 3


def test_several_numbers_in_quote_needs_review():
    clean, why = check_fact(fact(quote="Interest on lease liabilities 1,234.50 1,100"), SENT, YEARS)
    assert why == NEEDS_REVIEW_NUMBERS and clean["value"] == pytest.approx(1234.5e7)  # value kept for the owner to confirm
    board = {5: BOARD}
    clean, why = check_fact(fact(item="rnd_expenditure", value=421, page=5, quote="Total R&D expenditure 418 415 421 420"),
                            board, YEARS)
    assert why == NEEDS_REVIEW_NUMBERS
    from smt.sources.annual_report import Extraction
    ex = Extraction("T", ISIN, "x.pdf", "abc", FY, date(2026, 6, 1), "fake", calls=[
        {"items": ["rnd_expenditure"], "pages": {"5": BOARD},
         "response": _answer([fact(item="rnd_expenditure", value=421, page=5, quote="Total R&D expenditure 418 415 421 420")])}])
    (row,) = fact_rows(ex)
    assert row["verified"] is False and row["check_note"] == NEEDS_REVIEW_NUMBERS


def test_lease_quote_with_current_and_noncurrent_needs_review():
    page = {3: "Notes to consolidated financial statements\n(H crore)\nLease liabilities current and non-current 3,456"}
    _, why = check_fact(fact(item="lease_liabilities_current", value=3456,
                             quote="Lease liabilities current and non-current 3,456"), page, YEARS)
    assert why == NEEDS_REVIEW_LEASE
    _, why = check_fact(fact(item="lease_liabilities_noncurrent", value=12345,
                             quote="Lease liabilities - non-current 12,345"), SENT, YEARS)
    assert why is None


def test_item_the_report_says_does_not_exist_is_stored_as_not_found_with_quote(tmp_path):
    gov = "Corporate governance\nOther Details:\nThe Company does not have any employee stock\noption scheme.\nThe previous AGM"
    llm = FakeLLM(FeatureStore(), {"report_facts": _answer([fact()])})
    ex = extract(llm, tmp_path / "none.pdf", "TEST", ISIN, FY, date(2026, 6, 1), pages=["Cover", LEASE_NOTE, gov])
    rows = {r["item"]: r for r in fact_rows(ex) if r["fiscal_year_end"] == FY}
    esop = rows["share_based_payment_expense"]
    assert esop["value"] is None and esop["verified"] is False and esop["page"] == 3
    assert esop["quote"] == "The Company does not have any employee stock option scheme."
    assert esop["check_note"].startswith("not found: the report states")
    rnd = rows["rnd_expenditure"]
    assert rnd["value"] is None and rnd["quote"] is None and rnd["check_note"] == "not found: not mentioned in the report"
    ingest(ReportFactsRecord(ex), Lake(tmp_path / "lake"))  # stored through the contract with value null


def _lease_rows(interest, cur, noncur, cur25, noncur25):
    mk = lambda v: {"value": v * 1e7, "verified": True, "check_note": None}
    return {("interest_on_lease_liabilities", FY): mk(interest), ("lease_liabilities_current", FY): mk(cur),
            ("lease_liabilities_noncurrent", FY): mk(noncur), ("lease_liabilities_current", YEARS[1]): mk(cur25),
            ("lease_liabilities_noncurrent", YEARS[1]): mk(noncur25),
            ("depreciation_right_of_use", FY): mk(400)}


def test_implied_lease_interest_rate_check():
    from smt.sources.annual_report import lease_rate_check
    ok = _lease_rows(247, 461, 2712, 363, 2318)  # TITAN FY26: 247 / 2,927 = 8.4%
    lease_rate_check(ok, YEARS)
    assert all(r["verified"] for r in ok.values())
    low = _lease_rows(20, 461, 2712, 363, 2318)  # 0.7%
    lease_rate_check(low, YEARS)
    flagged = [k for k, r in low.items() if not r["verified"]]
    assert len(flagged) == 5 and ("depreciation_right_of_use", FY) not in flagged
    assert low[("lease_liabilities_current", FY)]["check_note"] == "needs review: implied lease interest rate 0.7% is outside 3% to 15%"
    high = _lease_rows(778, 1830, 453, 1554, 1000)  # 778 / 2,418.5 = 32%
    high[("interest_on_lease_liabilities", FY)]["check_note"] = NEEDS_REVIEW_NUMBERS
    lease_rate_check(high, YEARS)
    assert high[("interest_on_lease_liabilities", FY)]["check_note"].startswith(NEEDS_REVIEW_NUMBERS + "; needs review: implied")
    partial = _lease_rows(20, 461, 2712, 363, 2318)
    del partial[("lease_liabilities_current", YEARS[1])]
    lease_rate_check(partial, YEARS)  # cannot be worked out: no flag
    assert all(r["verified"] for r in partial.values())


def _stored_extraction(tmp_path):
    lake = Lake(tmp_path / "lake")
    llm = FakeLLM(FeatureStore(), {"report_facts": _answer([fact(quote="Interest on lease liabilities 1,234.50 1,100")])})
    ex = extract(llm, tmp_path / "none.pdf", "TEST", ISIN, FY, date(2026, 6, 1), pages=["Cover", "Blank", LEASE_NOTE])
    ingest(ReportFactsRecord(ex), lake)
    return lake, ex


def test_confirm_appends_a_new_row_and_survives_reparse(tmp_path):
    from smt.sources.owner_review import OwnerConfirmation, current_facts, find_row
    lake, ex = _stored_extraction(tmp_path)
    row = find_row(lake, ISIN, "interest_on_lease_liabilities", FY)
    assert row["verified"] is False and row["check_note"] == NEEDS_REVIEW_NUMBERS
    when = datetime(2026, 11, 15, 10, 0, tzinfo=timezone.utc)
    ingest(OwnerConfirmation(row, "FY26 column checked", confirmed_at=when), lake)
    new = find_row(lake, ISIN, "interest_on_lease_liabilities", FY)
    assert new["verified"] is True and new["check_note"] == "confirmed by owner review: FY26 column checked"
    assert new["value"] == row["value"] and new["source"] == "owner_review" and new["published_at"] == when
    # the old row is still stored, unchanged
    all_rows = lake.query(f"SELECT verified, check_note FROM {lake.read_sql('report_facts')} "
                          "WHERE item = 'interest_on_lease_liabilities' AND fiscal_year_end = DATE '2026-03-31'")
    assert sorted(all_rows, key=str) == sorted([(False, NEEDS_REVIEW_NUMBERS), (True, new["check_note"])], key=str)
    # re-running the checks on the LLM extraction does not undo the confirmation
    raw = next(p for p in (tmp_path / "lake").rglob("*.json.gz") if "annual_report_llm" in str(p))
    reparse(ReportFactsRecord(ex), lake, raw)
    assert find_row(lake, ISIN, "interest_on_lease_liabilities", FY)["verified"] is True
    # a backtest before the review sees the unconfirmed row
    before = [r for r in current_facts(lake, ISIN, as_of=datetime(2026, 9, 1, tzinfo=timezone.utc))
              if r["item"] == "interest_on_lease_liabilities" and r["fiscal_year_end"] == FY]
    assert before[0]["verified"] is False


def test_confirm_needs_a_value_and_a_note(tmp_path):
    from smt.sources.owner_review import OwnerConfirmation, find_row
    lake, _ = _stored_extraction(tmp_path)
    with pytest.raises(ValueError, match="nothing to confirm"):
        OwnerConfirmation(find_row(lake, ISIN, "rnd_expenditure", FY), "looks fine")
    with pytest.raises(ValueError, match="note"):
        OwnerConfirmation(find_row(lake, ISIN, "interest_on_lease_liabilities", FY), "  ")
    with pytest.raises(LookupError):
        find_row(lake, ISIN, "interest_on_lease_liabilities", date(2020, 3, 31))


def test_owner_entry_quote_is_checked_against_the_page(tmp_path):
    from smt.sources.owner_review import OwnerEntry, find_row
    page = "Expenditure on Research & Development\n (` in crore)\nc) Total 32.28 26.35\nd) Total R & D"
    kw = dict(instrument_id=ISIN, item="rd_expense_standalone", fiscal_year_end=FY, value="32.28", unit="crore", page=141,
              quote="c) Total 32.28 26.35", page_text=page, note="standalone only", filed_on=date(2026, 7, 3),
              pdf_file="T.pdf", pdf_sha256="abc")
    lake = Lake(tmp_path / "lake")
    ingest(OwnerEntry(**kw), lake)
    row = find_row(lake, ISIN, "rd_expense_standalone", FY)
    assert row["verified"] is True and row["value"] == pytest.approx(32.28e7) and row["check_note"] == "standalone only"
    assert row["extractor"] == "owner" and row["published_at"] == datetime(2026, 7, 3, 18, 30, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="not on page 141"):
        OwnerEntry(**{**kw, "quote": "c) Total 32.82 26.35"})
    with pytest.raises(ValueError, match="not a number in the quote"):
        OwnerEntry(**{**kw, "value": "23.28"})
    with pytest.raises(ValueError, match="LLM extraction"):
        OwnerEntry(**{**kw, "item": "rnd_expenditure"})
