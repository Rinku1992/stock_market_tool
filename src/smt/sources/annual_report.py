"""Annual report clean-up items (valuation stage B, step 1). The LLM reads, code checks.

    PDF -> text per page (code, pypdf)
        -> candidate pages per item by keywords (code; at most 3 pages and 12,000 characters per call;
           items whose pages overlap share one call, to save tokens)
        -> the LLM returns item, value as printed, unit, page and an exact quote as JSON (through llm.py
           and its budget guard)
        -> code checks each fact: the quote is on that page word for word (spaces and line breaks
           ignored), the printed value is one of the numbers in the quote, a unit (crore, lakh, million)
           is stated on that page, the page was one of those sent, the year is one asked for.
        -> value in absolute INR = printed value x unit (code). Failures are stored with verified = false
           and the reason, and are never used.
        -> needs review (verified = false, value kept for the owner to confirm the column): the quote holds
           more than one number (year or standalone/consolidated columns), or a lease liability quote names
           both current and non-current.
        -> items with no figure get a "not found" row (value null, never zero), with a code-found quote when
           the report says the item does not exist (for example "does not have any employee stock option
           scheme"), else the PDF pages that mention the item.

The raw payload kept in the lake holds the page texts sent and the LLM's answers, so the checks can be
re-run offline (reparse) without another LLM call.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
MAX_PAGES_PER_CALL = 3
MAX_CHARS_PER_CALL = 12_000
MAX_OUTPUT_TOKENS = 4_000
TASK = "report_facts"

UNITS = {"crore": Decimal(10_000_000), "lakh": Decimal(100_000), "million": Decimal(1_000_000)}
_UNIT_ALIASES = {"crore": "crore", "crores": "crore", "cr": "crore", "lakh": "lakh", "lakhs": "lakh", "lac": "lakh",
                 "lacs": "lakh", "million": "million", "millions": "million", "mn": "million"}
_UNIT_ON_PAGE = {"crore": re.compile(r"\bcrores?\b", re.I), "lakh": re.compile(r"\bla(kh|c)s?\b", re.I),
                 "million": re.compile(r"\bmillions?\b", re.I)}

# Headers that open a page of the consolidated statements (checked on TCS and TITAN FY26 reports).
_CONSOLIDATED_PAGE = re.compile(
    r"consolidated (balance sheet|statement of)|notes (forming part of|to) (the )?consolidated financial statements", re.I)
_STANDALONE = re.compile(r"standalone", re.I)
_BALANCE_SHEET = r"consolidated balance sheet"
PREFER_BONUS = 100


@dataclass(frozen=True)
class Item:
    name: str
    label: str  # what the LLM is asked for
    patterns: tuple[str, ...]  # all must match a page for it to be a candidate
    consolidated_section_only: bool = True
    prefer: str | None = None  # a page whose header matches this is picked first
    absent: str | None = None  # a sentence saying the item does not exist (searched on every page when not found)


ITEMS = (
    Item("lease_liabilities_current", "Lease liabilities, current (balance sheet, current liabilities)", (r"lease liabilit",),
         prefer=_BALANCE_SHEET),
    Item("lease_liabilities_noncurrent", "Lease liabilities, non-current (balance sheet, non-current liabilities)",
         (r"lease liabilit",), prefer=_BALANCE_SHEET),
    Item("interest_on_lease_liabilities", "Interest expense on lease liabilities for the year", (r"interest (expense )?on lease",)),
    Item("depreciation_right_of_use", "Depreciation on right-of-use assets for the year", (r"right[- ]of[- ]use", r"depreciation")),
    Item("share_based_payment_expense", "Share-based payment (employee stock option) expense charged to profit for the year",
         (r"share[- ]based payment|employee stock option|\bESOP\b|stock compensation",),
         absent=r"(does not have|has not (granted|issued))[^.]{0,60}\b(employee stock option|ESOP|share[- ]based)[^.]*\."),
    # R&D is usually disclosed in the Board's report, not in the statements; such a page must show the
    # consolidated figure (a "Consolidated" column), and the LLM is told to use that column.
    Item("rnd_expenditure", "Total research and development (R&D) expenditure for the year, consolidated",
         (r"research and development|\bR\s*&\s*D\b", r"consolidated"), consolidated_section_only=False),
)
ITEM_BY_NAME = {i.name: i for i in ITEMS}


def pdf_pages(path: str | Path) -> list[str]:
    """Text of each page; index 0 is PDF page 1."""
    from pypdf import PdfReader

    return [(p.extract_text() or "") for p in PdfReader(str(path)).pages]


def is_consolidated_page(text: str) -> bool:
    head = text[:250]
    return bool(_CONSOLIDATED_PAGE.search(head)) and not _STANDALONE.search(head)


def candidate_pages(pages: list[str], item: Item) -> dict[int, int]:
    """PDF page number -> score (keyword hits, +100 for a preferred page), for pages matching all patterns."""
    out = {}
    for i, text in enumerate(pages, start=1):
        if item.consolidated_section_only and not is_consolidated_page(text):
            continue
        hits = [len(re.findall(p, text, re.I)) for p in item.patterns]
        if all(hits):
            bonus = PREFER_BONUS if item.prefer and re.search(item.prefer, text[:250], re.I) else 0
            out[i] = sum(hits) + bonus
    return out


@dataclass
class PlannedCall:
    items: list[str]
    pages: list[int]


def plan_calls(pages: list[str], items=ITEMS) -> tuple[list[PlannedCall], list[str]]:
    """Group items into calls by shared pages. Returns (calls, items with no candidate page).

    Each call: first pages that cover the most items still uncovered, then (while there is room, at most
    3 pages and 12,000 characters) the next best pages for the same items, so each item gets more than one
    place to be found. Items left over get their own call.
    """
    cands = {it.name: candidate_pages(pages, it) for it in items}
    missing = [n for n, c in cands.items() if not c]
    remaining = {n for n, c in cands.items() if c}
    calls = []
    while remaining:
        chosen, covered, chars = [], set(), 0
        while len(chosen) < MAX_PAGES_PER_CALL:
            best = None
            for pg in sorted({p for n in remaining for p in cands[n]}):
                if pg in chosen or chars + len(pages[pg - 1]) > MAX_CHARS_PER_CALL:
                    continue
                new = {n for n in remaining - covered if pg in cands[n]}
                if not covered or new:  # cover first
                    key = (len(new), sum(cands[n].get(pg, 0) for n in remaining - covered))
                else:  # then fill for the items this call already asks for
                    key = (0, sum(cands[n].get(pg, 0) for n in covered))
                if key[1] > 0 and (best is None or key > best[0]):
                    best = (key, pg, new)
            if best is None:
                break
            chosen.append(best[1])
            covered |= best[2]
            chars += len(pages[best[1] - 1])
        if not chosen:  # every candidate page alone is too long
            missing += sorted(remaining)
            break
        calls.append(PlannedCall(sorted(covered), sorted(chosen)))
        remaining -= covered
    return calls, missing


SYSTEM = ("You copy figures from annual report pages. You never calculate, add up or convert numbers. "
          "Answer with JSON only.")


def build_prompt(company: str, years: list[date], items: list[str], pages: dict[int, str]) -> str:
    wanted = "\n".join(f'- "{n}": {ITEM_BY_NAME[n].label}' for n in items)
    yrs = ", ".join(str(y) for y in years)
    body = "\n\n".join(f"=== PAGE {n} ===\n{t}" for n, t in sorted(pages.items()))
    return f"""Company: {company}. Annual report pages are below, each starting with "=== PAGE n ===".

Find these items, CONSOLIDATED figures only, for the fiscal years ending {yrs} (where the pages show them):
{wanted}

Rules:
- If a table has Standalone and Consolidated columns, use the Consolidated column. Never use standalone figures.
- "value": the number exactly as printed (no conversion; a number in brackets is written without the brackets).
- "unit": the unit the page states for that number: "crore", "lakh" or "million".
- "page": the page number from the "=== PAGE n ===" line.
- "quote": one continuous piece of text copied exactly from that page, containing the item's label and the number.
- One fact per item and fiscal year. If an item is not on these pages, put it in "not_found". Do not guess.

Answer with this JSON only:
{{"facts": [{{"item": "...", "fiscal_year_end": "YYYY-MM-DD", "value": 0, "unit": "crore", "page": 0, "quote": "..."}}], "not_found": ["..."]}}

{body}"""


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def quote_numbers(quote: str) -> list[Decimal]:
    out = []
    for m in _NUM.finditer(quote):
        try:
            out.append(Decimal(m.group(0).replace(",", "")))
        except InvalidOperation:
            pass
    return out


def check_fact(fact: dict, pages_sent: dict[int, str], years: list[date], known=ITEM_BY_NAME) -> tuple[dict, str | None]:
    """-> (clean fields, failure reason or None). Never raises on bad LLM output."""
    item = fact.get("item")
    out = {"item": item, "value": None, "unit": None, "page": None, "quote": fact.get("quote") or None,
           "fiscal_year_end": None}
    if item not in known:
        return out, f"unknown item {item!r}"
    try:
        fy = date.fromisoformat(str(fact.get("fiscal_year_end")))
    except ValueError:
        return out, f"fiscal year end {fact.get('fiscal_year_end')!r} is not a date"
    out["fiscal_year_end"] = fy
    if fy not in years:
        return out, f"fiscal year end {fy} was not asked for"
    try:
        page = int(fact.get("page"))
    except (TypeError, ValueError):
        return out, f"page {fact.get('page')!r} is not a number"
    out["page"] = page
    if page not in pages_sent:
        return out, f"page {page} was not one of the pages sent ({', '.join(map(str, sorted(pages_sent)))})"
    unit_raw = str(fact.get("unit") or "").strip().lower()
    unit = _UNIT_ALIASES.get(unit_raw)
    out["unit"] = unit or (unit_raw or None)
    if not unit_raw:
        return out, "no unit stated"
    if unit is None:
        return out, f"unit {unit_raw!r} is not crore, lakh or million"
    quote = out["quote"]
    if not quote:
        return out, "no quote"
    if _squash(quote) not in _squash(pages_sent[page]):
        return out, f"quote is not on page {page} word for word"
    if not _UNIT_ON_PAGE[unit].search(pages_sent[page]):
        return out, f"page {page} does not state the unit '{unit}'"
    try:
        printed = abs(Decimal(str(fact.get("value")).replace(",", "").strip("() ")))  # "1,820" or "(77)" as printed
    except InvalidOperation:
        return out, f"value {fact.get('value')!r} is not a number"
    if printed not in quote_numbers(quote):
        return out, f"value {printed} is not a number in the quote"
    out["value"] = float(printed * UNITS[unit])
    return out, needs_review(item, quote)


NEEDS_REVIEW_NUMBERS = "needs review: several numbers in quote"
NEEDS_REVIEW_LEASE = "needs review: current and non-current both in quote"
NOT_FOUND = "not found"
_NONCURRENT = re.compile(r"non[\s-]*current", re.I)
_CURRENT = re.compile(r"\bcurrent\b", re.I)


def needs_review(item: str, quote: str) -> str | None:
    """A passed fact whose column or line code cannot pin down: kept for the owner, never used as verified."""
    if len(quote_numbers(quote)) > 1:
        return NEEDS_REVIEW_NUMBERS
    if item.startswith("lease_liabilities_") and _NONCURRENT.search(quote) and _CURRENT.search(_NONCURRENT.sub("", quote)):
        return NEEDS_REVIEW_LEASE
    return None


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def find_absence(pages: list[str], item: Item) -> dict:
    """For an item with no figure: a sentence saying it does not exist (page, quote), and the pages that mention it."""
    mention = re.compile(item.patterns[0].replace(" ", r"\s+"), re.I)
    found = {"item": item.name, "page": None, "quote": None,
             "mentions": [i for i, t in enumerate(pages, 1) if mention.search(t)]}
    if item.absent:
        for i, t in enumerate(pages, 1):
            m = re.search(item.absent, _flat(t), re.I)
            if m:
                flat = _flat(t)
                stops = [x.end() for x in re.finditer(r"[.:;?!]\s", flat[:m.start()])]  # start of the sentence
                found["page"] = i
                found["quote"] = re.sub(r"^\W+", "", flat[(stops[-1] if stops else 0):m.end()]).strip()
                break
    return found


def absence_note(a: dict) -> str:
    if a.get("quote"):
        return f"{NOT_FOUND}: the report states the item does not exist (PDF page {a['page']})"
    where = a.get("mentions") or []
    if not where:
        return f"{NOT_FOUND}: not mentioned in the report"
    more = f" and {len(where) - 8} more" if len(where) > 8 else ""
    return (f"{NOT_FOUND}: no consolidated figure; mentioned on PDF pages {', '.join(map(str, where[:8]))}{more} "
            "(not consolidated statement pages)")


LEASE_RATE_MIN, LEASE_RATE_MAX = 0.03, 0.15
LEASE_ITEMS = ("interest_on_lease_liabilities", "lease_liabilities_current", "lease_liabilities_noncurrent")


def lease_rate_check(rows: dict[tuple, dict], years: list[date]) -> None:
    """Implied lease interest rate = interest on lease liabilities (latest year) / average total lease
    liabilities (current + non-current, both years). Outside 3% to 15%: every lease row with a value is
    flagged (verified = false). Uses values that passed the checks, verified or held for review."""
    fy, prev = years
    val = {k: r["value"] for k, r in rows.items() if r.get("value") is not None}
    need = [("interest_on_lease_liabilities", fy)] + [(i, y) for i in LEASE_ITEMS[1:] for y in (fy, prev)]
    if any(k not in val for k in need):
        return
    avg = (val[("lease_liabilities_current", fy)] + val[("lease_liabilities_noncurrent", fy)]
           + val[("lease_liabilities_current", prev)] + val[("lease_liabilities_noncurrent", prev)]) / 2
    rate = val[("interest_on_lease_liabilities", fy)] / avg if avg else float("inf")
    if LEASE_RATE_MIN <= rate <= LEASE_RATE_MAX:
        return
    flag = (f"needs review: implied lease interest rate {rate:.1%} is outside "
            f"{LEASE_RATE_MIN:.0%} to {LEASE_RATE_MAX:.0%}")
    for (item, _), r in rows.items():
        if item in LEASE_ITEMS and r.get("value") is not None:
            r["verified"] = False
            r["check_note"] = flag if not r["check_note"] else f"{r['check_note']}; {flag}"


def parse_response(text: str) -> dict:
    """The LLM's JSON, also when wrapped in ``` fences. Raises ValueError if there is none."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in the answer")
    return json.loads(text[start:end + 1])


def published_at_for(filed_on: date) -> datetime:
    """A filing date has no time: treat the report as known from the end of that day (India time)."""
    return datetime.combine(filed_on + timedelta(days=1), time(0), IST).astimezone(timezone.utc)


@dataclass
class Extraction:
    """Everything needed to check the facts again later: the pages sent and the LLM's answers."""
    symbol: str
    instrument_id: str
    pdf_file: str
    pdf_sha256: str
    fiscal_year_end: date
    filed_on: date | None
    extractor: str
    calls: list[dict] = field(default_factory=list)  # {"items", "pages": {n: text}, "response"}
    not_searched: list[str] = field(default_factory=list)  # items with no candidate page
    absent: list[dict] = field(default_factory=list)  # find_absence() for each not_searched item


def extract(llm, pdf_path: str | Path, symbol: str, instrument_id: str, fiscal_year_end: date,
            filed_on: date | None, pages: list[str] | None = None) -> Extraction:
    pdf_path = Path(pdf_path)
    pages = pages if pages is not None else pdf_pages(pdf_path)
    years = [fiscal_year_end, fiscal_year_end.replace(year=fiscal_year_end.year - 1)]
    planned, missing = plan_calls(pages)
    ex = Extraction(symbol, instrument_id, pdf_path.name, hashlib.sha256(pdf_path.read_bytes()).hexdigest()
                    if pdf_path.exists() else "", fiscal_year_end, filed_on, llm.model_for(TASK), not_searched=missing,
                    absent=[find_absence(pages, ITEM_BY_NAME[n]) for n in missing])
    for pc in planned:
        sent = {n: pages[n - 1] for n in pc.pages}
        text = llm.complete(task=TASK, system=SYSTEM, prompt=build_prompt(symbol, years, pc.items, sent),
                            max_tokens=MAX_OUTPUT_TOKENS)
        ex.calls.append({"items": pc.items, "pages": {str(k): v for k, v in sent.items()}, "response": text})
    return ex


def fact_rows(ex: Extraction) -> list[dict]:
    """Check every fact the LLM gave. One row per item and year; failures kept with verified = false."""
    years = [ex.fiscal_year_end, ex.fiscal_year_end.replace(year=ex.fiscal_year_end.year - 1)]
    published = published_at_for(ex.filed_on) if ex.filed_on else None
    rows: dict[tuple, dict] = {}
    unplaced = 0
    for call in ex.calls:
        sent = {int(k): v for k, v in call["pages"].items()}
        try:
            facts = parse_response(call["response"]).get("facts") or []
        except (ValueError, json.JSONDecodeError) as e:
            facts = [{"item": n, "fiscal_year_end": str(years[0]), "_error": f"answer was not valid JSON ({e})"}
                     for n in call["items"]]
        for f in facts:
            clean, why = check_fact(f, sent, years) if "_error" not in f else (
                {"item": f["item"], "value": None, "unit": None, "page": None, "quote": None,
                 "fiscal_year_end": years[0]}, f["_error"])
            if clean["item"] not in ITEM_BY_NAME or clean["fiscal_year_end"] not in years:
                unplaced += 1  # cannot be stored under the contract (unknown item or year); counted only
                continue
            row = {"instrument_id": ex.instrument_id, "extractor": ex.extractor, "published_at": published,
                   "source_ref": f"{ex.pdf_file} sha256:{ex.pdf_sha256[:12]}", **clean,
                   "verified": why is None, "check_note": why}
            key = (clean["item"], clean["fiscal_year_end"])
            old = rows.get(key)
            if old is None or (row["verified"] and not old["verified"]):
                rows[key] = row
            elif row["verified"] and old["verified"] and row["value"] != old["value"]:
                for r in (old, row):
                    r["verified"], r["check_note"] = False, "two different values were given for this item and year"
    lease_rate_check(rows, years)
    base = {"instrument_id": ex.instrument_id, "extractor": ex.extractor, "published_at": published,
            "source_ref": f"{ex.pdf_file} sha256:{ex.pdf_sha256[:12]}", "fiscal_year_end": years[0], "value": None,
            "unit": None, "verified": False}
    for a in ex.absent:  # no candidate page: never asked, never zero
        rows.setdefault((a["item"], years[0]), {**base, "item": a["item"], "page": a.get("page"), "quote": a.get("quote"),
                                                "check_note": absence_note(a)})
    for call in ex.calls:  # asked, but no fact came back for the latest year
        for n in call["items"]:
            rows.setdefault((n, years[0]), {**base, "item": n, "page": None, "quote": None,
                                            "check_note": f"{NOT_FOUND} on the pages sent"})
    return list(rows.values())


class ReportFactsRecord:
    """Adapter: fetch() serialises an Extraction; parse() runs the checks on it."""

    name = "annual_report_llm"
    dataset = "report_facts"
    raw_ext = "json"

    def __init__(self, ex: Extraction) -> None:
        if ex.filed_on is None:
            raise ValueError("the annual report's filing date is needed (published_at); it is never guessed")
        self.ex = ex

    @property
    def context(self) -> dict:
        return {"instrument_id": self.ex.instrument_id, "fiscal_year_end": str(self.ex.fiscal_year_end),
                "pdf_sha256": self.ex.pdf_sha256}

    def fetch(self) -> bytes:
        d = dict(self.ex.__dict__)
        return json.dumps(d, default=str, sort_keys=True).encode()

    def parse(self, payload: bytes) -> list[dict]:
        d = json.loads(payload)
        d["fiscal_year_end"] = date.fromisoformat(d["fiscal_year_end"])
        d["filed_on"] = date.fromisoformat(d["filed_on"]) if d.get("filed_on") else None
        return fact_rows(Extraction(**d))
