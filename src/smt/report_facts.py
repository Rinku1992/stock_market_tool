"""Extract clean-up items from an annual report (valuation stage B, step 1).

    SMT_LLM_DRY_RUN=1 python -m smt.report_facts TCS data/annual_reports/TCS_FY26.pdf --fy-end 2026-03-31
    python -m smt.report_facts TCS data/annual_reports/TCS_FY26.pdf --fy-end 2026-03-31 --filed-on YYYY-MM-DD

--filed-on is the date the annual report was filed (it becomes published_at). It is needed to store
anything and is never guessed. A dry run makes no LLM call and stores nothing.

Owner review (appends a new row, never edits one):

    python -m smt.report_facts confirm TITAN:lease_liabilities_current:2026-03-31 --note "why"
    python -m smt.report_facts correct TCS:lease_liabilities_noncurrent:2026-03-31 --value 9453 --note "why"
    python -m smt.report_facts add TITAN data/annual_reports/TITAN_FY26.pdf --item rd_expense_standalone \
        --fy-end 2026-03-31 --value 32.28 --unit crore --page 141 --quote "c) Total 32.28 26.35" \
        --filed-on 2026-07-03 --note "why"
"""

import argparse
import hashlib
import os
import sys
from datetime import date
from pathlib import Path

from smt.data.adapter import ingest
from smt.data.lake import Lake
from smt.data.members import symbol_to_isin
from smt.llm import ClaudeClient
from smt.sources.annual_report import NOT_FOUND, ReportFactsRecord, extract, fact_rows, pdf_pages
from smt.sources.owner_review import OwnerConfirmation, OwnerCorrection, OwnerEntry, find_row
from smt.store import FeatureStore


def _lake() -> Lake:
    return Lake(os.environ.get("SMT_LAKE_PATH", "data/lake"))


def _isin(lake: Lake, symbol: str, given: str | None, p: argparse.ArgumentParser) -> str:
    isin = given or symbol_to_isin(lake).get(symbol.upper())
    if not isin:
        p.error(f"{symbol} is not in your holdings or the Nifty 100 list. Add --isin.")
    return isin


def confirm(argv: list[str]) -> None:
    p = argparse.ArgumentParser(prog="smt.report_facts confirm")
    p.add_argument("row", help="SYMBOL:item:fiscal-year-end, e.g. TITAN:lease_liabilities_current:2026-03-31")
    p.add_argument("--note", required=True, help="why the row is confirmed")
    p.add_argument("--isin")
    args = p.parse_args(argv)
    try:
        symbol, item, fy = args.row.split(":")
        fy = date.fromisoformat(fy)
    except ValueError:
        p.error("row must be SYMBOL:item:YYYY-MM-DD")
    lake = _lake()
    row = find_row(lake, _isin(lake, symbol, args.isin, p), item, fy)
    adapter = OwnerConfirmation(row, args.note)
    ingest(adapter, lake)
    new = adapter.parse(adapter.fetch())[0]
    print(f"Confirmed {symbol.upper()} {item} {fy}: Rs {new['value'] / 1e7:,.2f} crore, page {new['page']}. "
          f"New row appended (verified True; was: {row['check_note'] or '-'}).")


def correct(argv: list[str]) -> None:
    p = argparse.ArgumentParser(prog="smt.report_facts correct")
    p.add_argument("row", help="SYMBOL:item:fiscal-year-end")
    p.add_argument("--value", required=True, help="the right number in the row's unit, e.g. 9453 for Rs 9,453 crore")
    p.add_argument("--note", required=True, help="why the value is corrected")
    p.add_argument("--isin")
    args = p.parse_args(argv)
    try:
        symbol, item, fy = args.row.split(":")
        fy = date.fromisoformat(fy)
    except ValueError:
        p.error("row must be SYMBOL:item:YYYY-MM-DD")
    lake = _lake()
    row = find_row(lake, _isin(lake, symbol, args.isin, p), item, fy)
    adapter = OwnerCorrection(row, args.value, args.note)
    ingest(adapter, lake)
    new = adapter.parse(adapter.fetch())[0]
    was = f"Rs {row['value'] / 1e7:,.2f} crore" if row["value"] is not None else "-"
    print(f"Corrected {symbol.upper()} {item} {fy}: {was} -> Rs {new['value'] / 1e7:,.2f} crore ({new['unit']}). "
          f"New row appended (verified True); quote kept: {new['quote']!r}")


def add(argv: list[str]) -> None:
    p = argparse.ArgumentParser(prog="smt.report_facts add")
    p.add_argument("symbol"); p.add_argument("pdf")
    p.add_argument("--item", required=True)
    p.add_argument("--fy-end", type=date.fromisoformat, required=True,
                   help="the date the figure is for: fiscal year end, or the balance sheet date for a balance sheet item "
                        "such as gold_on_loan (may be a half-year end, e.g. 2026-09-30)")
    p.add_argument("--value", required=True, help="as printed, e.g. 32.28"); p.add_argument("--unit", required=True)
    p.add_argument("--page", type=int, required=True, help="PDF page number, from 1 (annual report or results PDF)")
    p.add_argument("--quote", required=True, help="exact text from that page")
    p.add_argument("--filed-on", type=date.fromisoformat, required=True, help="date the PDF was filed (becomes published_at)")
    p.add_argument("--note", required=True); p.add_argument("--isin")
    args = p.parse_args(argv)
    lake = _lake()
    pages = pdf_pages(args.pdf)
    if not 1 <= args.page <= len(pages):
        p.error(f"page {args.page} is not in the PDF (1 to {len(pages)})")
    adapter = OwnerEntry(instrument_id=_isin(lake, args.symbol, args.isin, p), item=args.item, fiscal_year_end=args.fy_end,
                         value=args.value, unit=args.unit, page=args.page, quote=args.quote,
                         page_text=pages[args.page - 1], note=args.note, filed_on=args.filed_on,
                         pdf_file=Path(args.pdf).name, pdf_sha256=hashlib.sha256(Path(args.pdf).read_bytes()).hexdigest())
    ingest(adapter, lake)
    new = adapter.parse(adapter.fetch())[0]
    print(f"Added {args.symbol.upper()} {args.item} {args.fy_end}: Rs {new['value'] / 1e7:,.2f} crore, page {args.page}, "
          f"verified True; check note: {new['check_note']}\n    quote: {new['quote']!r}")


def main(argv: list[str] | None = None) -> None:
    from dotenv import load_dotenv

    load_dotenv(".env")
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "confirm":
        return confirm(argv[1:])
    if argv and argv[0] == "correct":
        return correct(argv[1:])
    if argv and argv[0] == "add":
        return add(argv[1:])
    p = argparse.ArgumentParser(prog="smt.report_facts")
    p.add_argument("symbol")
    p.add_argument("pdf")
    p.add_argument("--fy-end", type=date.fromisoformat, required=True, help="fiscal year end of the report, e.g. 2026-03-31")
    p.add_argument("--filed-on", type=date.fromisoformat, help="date the annual report was filed (needed to store)")
    p.add_argument("--isin")
    args = p.parse_args(argv)
    lake = _lake()
    isin = _isin(lake, args.symbol, args.isin, p)
    store = FeatureStore(os.environ.get("SMT_DB_PATH", "data/smt.duckdb"))
    llm = ClaudeClient(store)
    if not llm.dry_run and args.filed_on is None:
        p.error("--filed-on (the date the annual report was filed) is needed to store facts. It is never guessed.")
    ex = extract(llm, args.pdf, args.symbol.upper(), isin, args.fy_end, args.filed_on)
    n = len(ex.calls)
    print(f"\n{args.symbol.upper()}: {n} LLM call(s) planned; items with no candidate page: {', '.join(ex.not_searched) or 'none'}")
    for c in ex.calls:
        print(f"  items {', '.join(c['items'])} on PDF pages {', '.join(c['pages'])} "
              f"({sum(len(t) for t in c['pages'].values()):,} characters)")
    if llm.dry_run:
        print(f"Dry run: estimated tokens {llm.dry_run_tokens:,} (input estimate + full output allowance). Nothing stored.")
        return
    ingest(ReportFactsRecord(ex), lake)
    print(f"\nStored in report_facts. Tokens used this run: {llm.run_tokens:,}; cache hits: {llm.cache_hits}.")
    groups = {"VERIFIED": [], "NEEDS REVIEW (confirm the column)": [], "NOT FOUND (value left empty, never zero)": [],
              "NOT VERIFIED (failed a check; never used)": []}
    for r in sorted(fact_rows(ex), key=lambda r: (r["item"], r["fiscal_year_end"])):
        note = r["check_note"] or ""
        key = ("VERIFIED" if r["verified"] else "NEEDS REVIEW (confirm the column)" if note.startswith("needs review")
               else "NOT FOUND (value left empty, never zero)" if note.startswith(NOT_FOUND)
               else "NOT VERIFIED (failed a check; never used)")
        groups[key].append(r)
    for title, rows in groups.items():
        print(f"\n{title}: {len(rows)}")
        for r in rows:
            v = f"Rs {r['value'] / 1e7:,.2f} crore" if r["value"] is not None else "-"
            print(f"- {r['item']} FY ending {r['fiscal_year_end']}: value {v}; page {r['page']}; verified {r['verified']}; "
                  f"check note: {r['check_note'] or '-'}\n    quote: {r['quote']!r}")


if __name__ == "__main__":
    main()
