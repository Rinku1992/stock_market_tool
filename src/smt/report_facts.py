"""Extract clean-up items from an annual report (valuation stage B, step 1).

    SMT_LLM_DRY_RUN=1 python -m smt.report_facts TCS data/annual_reports/TCS_FY26.pdf --fy-end 2026-03-31
    python -m smt.report_facts TCS data/annual_reports/TCS_FY26.pdf --fy-end 2026-03-31 --filed-on YYYY-MM-DD

--filed-on is the date the annual report was filed (it becomes published_at). It is needed to store
anything and is never guessed. A dry run makes no LLM call and stores nothing.
"""

import argparse
import os
from datetime import date

from smt.data.adapter import ingest
from smt.data.lake import Lake
from smt.data.members import symbol_to_isin
from smt.llm import ClaudeClient
from smt.sources.annual_report import NOT_FOUND, ReportFactsRecord, extract, fact_rows
from smt.store import FeatureStore


def main(argv: list[str] | None = None) -> None:
    from dotenv import load_dotenv

    load_dotenv(".env")
    p = argparse.ArgumentParser(prog="smt.report_facts")
    p.add_argument("symbol")
    p.add_argument("pdf")
    p.add_argument("--fy-end", type=date.fromisoformat, required=True, help="fiscal year end of the report, e.g. 2026-03-31")
    p.add_argument("--filed-on", type=date.fromisoformat, help="date the annual report was filed (needed to store)")
    p.add_argument("--isin")
    args = p.parse_args(argv)
    lake = Lake(os.environ.get("SMT_LAKE_PATH", "data/lake"))
    isin = args.isin or symbol_to_isin(lake).get(args.symbol.upper())
    if not isin:
        p.error(f"{args.symbol} is not in your holdings or the Nifty 100 list. Add --isin.")
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
