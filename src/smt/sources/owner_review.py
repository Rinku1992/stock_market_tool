"""Owner review of report_facts: confirm a stored row, or add a figure the owner read in the report.

Nothing is edited. Both append a new row through the contract, with the payload kept as raw:

- confirm: a copy of the current row with verified = true and check_note "confirmed by owner review: <note>".
  published_at = the time of confirmation, so it is a new key: a later reparse of the LLM extraction
  (published_at = filing day) cannot overwrite it, and a backtest before the review sees the unconfirmed row.
- correct: like confirm, but with the owner's value (in the row's stated unit) instead of the extracted one.
  The original quote and page are kept; the wrong row stays stored and is never used (an older row).
- add: an owner-entered figure (item, value, unit, page, quote). Code still checks the quote is on that
  page of the PDF word for word, the unit is stated there and the value is a number in the quote.
  published_at = end of the filing day, like any figure from the report.

Readers take the row with the latest published_at per instrument, fiscal year and item (current_facts).
"""

import json
from datetime import date, datetime, timezone

from smt.data.lake import Lake
from decimal import Decimal, InvalidOperation

from smt.sources.annual_report import UNITS, check_fact, published_at_for

OWNER_ITEMS = {"rd_expense_standalone", "rd_and_innovation_total"}
CONFIRM_PREFIX = "confirmed by owner review: "
CORRECT_PREFIX = "corrected by owner review: "


def current_facts(lake: Lake, instrument_id: str | None = None, as_of: datetime | None = None) -> list[dict]:
    """The latest row per (instrument, fiscal year, item), as known at as_of (default: now)."""
    where, params = ["1=1"], []
    if instrument_id:
        where.append("instrument_id = ?"); params.append(instrument_id)
    if as_of:
        where.append("published_at <= ?"); params.append(as_of)
    cols = ["instrument_id", "fiscal_year_end", "item", "value", "unit", "page", "quote", "extractor", "verified",
            "check_note", "source", "raw_sha256", "epoch(published_at)"]
    sql = (f"SELECT {', '.join(cols)} FROM (SELECT *, row_number() OVER (PARTITION BY instrument_id, fiscal_year_end, item "
           f"ORDER BY published_at DESC, ingested_at DESC) AS rn FROM {lake.read_sql('report_facts')} "
           f"WHERE {' AND '.join(where)}) WHERE rn = 1 ORDER BY instrument_id, item, fiscal_year_end")
    out = []
    for r in lake.query(sql, params):
        d = dict(zip(cols[:-1] + ["published_at"], r))
        d["published_at"] = datetime.fromtimestamp(d["published_at"], timezone.utc)
        out.append(d)
    return out


def find_row(lake: Lake, instrument_id: str, item: str, fiscal_year_end: date) -> dict:
    for r in current_facts(lake, instrument_id):
        if r["item"] == item and r["fiscal_year_end"] == fiscal_year_end:
            return r
    raise LookupError(f"no report_facts row for {instrument_id} {item} {fiscal_year_end}")


class OwnerConfirmation:
    """Adapter: a copy of one current row, confirmed by the owner with a note."""

    name = "owner_review"
    dataset = "report_facts"
    raw_ext = "json"

    def __init__(self, row: dict, note: str, confirmed_at: datetime | None = None) -> None:
        if row.get("value") is None:
            raise ValueError("this row has no value (not found or failed a check); there is nothing to confirm")
        if not note.strip():
            raise ValueError("a note saying why is needed to confirm a row")
        self.row, self.note = row, note.strip()
        self.confirmed_at = confirmed_at or datetime.now(timezone.utc)

    @property
    def context(self) -> dict:
        return {"instrument_id": self.row["instrument_id"], "item": self.row["item"],
                "fiscal_year_end": str(self.row["fiscal_year_end"]), "action": "confirm"}

    def fetch(self) -> bytes:
        keep = ("instrument_id", "fiscal_year_end", "item", "value", "unit", "page", "quote", "extractor",
                "check_note", "raw_sha256")
        return json.dumps({"row": {k: self.row.get(k) for k in keep}, "note": self.note,
                           "confirmed_at": self.confirmed_at.isoformat()}, default=str, sort_keys=True).encode()

    def parse(self, payload: bytes) -> list[dict]:
        d = json.loads(payload)
        r = d["row"]
        return [{"instrument_id": r["instrument_id"], "fiscal_year_end": date.fromisoformat(r["fiscal_year_end"]),
                 "item": r["item"], "value": r["value"], "unit": r["unit"], "page": r["page"], "quote": r["quote"],
                 "extractor": r["extractor"], "verified": True, "check_note": CONFIRM_PREFIX + d["note"],
                 "published_at": datetime.fromisoformat(d["confirmed_at"]),
                 "source_ref": f"confirms raw {(r.get('raw_sha256') or '')[:12]} (was: {r.get('check_note') or '-'})"}]


class OwnerCorrection(OwnerConfirmation):
    """Adapter: a copy of one current row with the owner's value. Quote and page are kept as they were."""

    name = "owner_correction"

    def __init__(self, row: dict, value: str | float, note: str, confirmed_at: datetime | None = None) -> None:
        unit = row.get("unit")
        if unit not in UNITS:
            raise ValueError(f"this row has no usable unit ({unit!r}); it cannot be corrected, add it instead")
        if not note.strip():
            raise ValueError("a note saying why is needed to correct a row")
        try:
            printed = Decimal(str(value).replace(",", ""))
        except InvalidOperation:
            raise ValueError(f"value {value!r} is not a number") from None
        self.row, self.note = row, note.strip()
        self.printed = printed
        self.confirmed_at = confirmed_at or datetime.now(timezone.utc)

    @property
    def context(self) -> dict:
        return {**super().context, "action": "correct"}

    def fetch(self) -> bytes:
        d = json.loads(super().fetch())
        d["new_value_as_printed"] = str(self.printed)
        return json.dumps(d, sort_keys=True).encode()

    def parse(self, payload: bytes) -> list[dict]:
        (row,) = super().parse(payload)
        d = json.loads(payload)
        old = d["row"]["value"]
        row["value"] = float(Decimal(d["new_value_as_printed"]) * UNITS[row["unit"]])
        row["check_note"] = CORRECT_PREFIX + d["note"]
        row["source_ref"] = (f"corrects raw {(d['row'].get('raw_sha256') or '')[:12]} "
                             f"(was {old / 1e7 if old is not None else '-'} crore: {d['row'].get('check_note') or '-'})")
        return [row]


class OwnerEntry:
    """Adapter: a figure the owner read in the report. The quote is checked against the PDF page text."""

    name = "owner_entry"
    dataset = "report_facts"
    raw_ext = "json"

    def __init__(self, *, instrument_id: str, item: str, fiscal_year_end: date, value: float, unit: str, page: int,
                 quote: str, page_text: str, note: str, filed_on: date, pdf_file: str, pdf_sha256: str) -> None:
        if item not in OWNER_ITEMS:
            raise ValueError(f"owner entries are for {sorted(OWNER_ITEMS)}; {item!r} comes from the LLM extraction")
        if filed_on is None:
            raise ValueError("the annual report's filing date is needed (published_at); it is never guessed")
        self.d = {"instrument_id": instrument_id, "item": item, "fiscal_year_end": str(fiscal_year_end), "value": value,
                  "unit": unit, "page": page, "quote": quote, "page_text": page_text, "note": note,
                  "filed_on": str(filed_on), "pdf_file": pdf_file, "pdf_sha256": pdf_sha256}
        self.parse(self.fetch())  # refuse at once if the checks fail

    @property
    def context(self) -> dict:
        return {"instrument_id": self.d["instrument_id"], "item": self.d["item"],
                "fiscal_year_end": self.d["fiscal_year_end"], "action": "add"}

    def fetch(self) -> bytes:
        return json.dumps(self.d, sort_keys=True).encode()

    def parse(self, payload: bytes) -> list[dict]:
        d = json.loads(payload)
        fy = date.fromisoformat(d["fiscal_year_end"])
        fact = {k: d[k] for k in ("item", "value", "unit", "page", "quote")} | {"fiscal_year_end": d["fiscal_year_end"]}
        clean, why = check_fact(fact, {d["page"]: d["page_text"]}, [fy], known=OWNER_ITEMS)
        if why and not why.startswith("needs review"):  # the owner has chosen the column; other checks must pass
            raise ValueError(f"owner entry failed a check: {why}")
        return [{"instrument_id": d["instrument_id"], **clean, "extractor": "owner", "verified": True,
                 "check_note": d["note"], "published_at": published_at_for(date.fromisoformat(d["filed_on"])),
                 "source_ref": f"{d['pdf_file']} sha256:{d['pdf_sha256'][:12]}"}]
