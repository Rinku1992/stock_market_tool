"""Files the owner downloads BY HAND from official NSE pages.

There is no automated access to nseindia.com in this project: whether NSE allows it is not
verified. These adapters only read a local file the owner saved.
"""

import csv
import io
from datetime import date, datetime, timezone
from pathlib import Path

UTC = timezone.utc


class IndexListError(ValueError):
    pass


def _pick(headers: list[str], needle: str) -> str | None:
    for h in headers:
        if needle in h.strip().lower():
            return h
    return None


def parse_index_list(payload: bytes) -> list[tuple[str, str, str | None]]:
    """(symbol, ISIN, industry) from an index constituents CSV. Industry is None if there is no such column.

    Matches columns by name ("symbol", "isin", optional "series" and "industry"), so small header
    changes do not break it. Fails loudly if the symbol or ISIN column is not found.
    """
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
    headers = reader.fieldnames or []
    sym, isin, series, ind = _pick(headers, "symbol"), _pick(headers, "isin"), _pick(headers, "series"), _pick(headers, "industry")
    if not sym or not isin:
        raise IndexListError(f"need a Symbol column and an ISIN column, found {headers}")
    out, seen = [], set()
    for r in reader:
        s, i = (r.get(sym) or "").strip().upper(), (r.get(isin) or "").strip().upper()
        if not s and not i:
            continue
        if series and (r.get(series) or "").strip().upper() not in ("", "EQ"):
            continue
        if len(i) != 12 or not i.startswith("IN"):
            raise IndexListError(f"row for {s!r} has a bad ISIN {i!r}")
        if i not in seen:
            seen.add(i)
            out.append((s, i, ((r.get(ind) or "").strip() or None) if ind else None))
    if not out:
        raise IndexListError("no stock rows found")
    return out


class NseIndexListFile:
    """An index constituents list saved by hand, stored as index_members."""

    name = "nse_manual_file"
    dataset = "index_members"
    raw_ext = "csv"

    def __init__(self, path: str | Path, index_id: str, as_of: date) -> None:
        self.path = Path(path)
        self.index_id = index_id
        self.as_of = as_of  # the day the list was downloaded: membership is known from then

    @property
    def context(self) -> dict:
        return {"index_id": self.index_id, "file": self.path.name, "as_of": self.as_of.isoformat()}

    def fetch(self) -> bytes:
        return self.path.read_bytes()

    def parse(self, payload: bytes) -> list[dict]:
        published = datetime(self.as_of.year, self.as_of.month, self.as_of.day, tzinfo=UTC)
        return [{"index_id": self.index_id, "instrument_id": isin, "valid_from": self.as_of, "valid_to": None,
                 "industry": industry, "published_at": published, "source_ref": sym}
                for sym, isin, industry in parse_index_list(payload)]
