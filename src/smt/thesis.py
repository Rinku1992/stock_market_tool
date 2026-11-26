"""Thesis store: the owner's written reason for holding a stock.

    python -m smt.thesis set SYMBOL "why I own it"
    python -m smt.thesis show SYMBOL

Stored in the lake (dataset "theses") with the time it was written. Append-only: a new entry never overwrites an
old one; show prints them all, latest first. Checking a thesis against the facts is for later (LLM reviewers).
"""

import argparse
import json
from datetime import datetime, timezone

from smt.data.adapter import ingest
from smt.data.lake import Lake


class ThesisEntry:
    name, dataset, raw_ext = "owner_thesis", "theses", "json"

    def __init__(self, instrument_id: str, symbol: str, text: str, now: datetime | None = None):
        if not text.strip():
            raise ValueError("thesis text is empty")
        self.row = {"instrument_id": instrument_id, "symbol": symbol.upper(), "text": text.strip(),
                    "published_at": (now or datetime.now(timezone.utc)).isoformat()}

    @property
    def context(self) -> dict:
        return {"instrument_id": self.row["instrument_id"]}

    def fetch(self) -> bytes:
        return json.dumps([self.row]).encode()

    def parse(self, payload: bytes) -> list[dict]:
        return [{**r, "published_at": datetime.fromisoformat(r["published_at"])} for r in json.loads(payload)]


def entries(lake: Lake, instrument_id: str) -> list[tuple[str, datetime]]:
    if not lake.has("theses"):
        return []
    rows = lake.query(f"SELECT text, epoch(published_at) FROM {lake.read_sql('theses')} WHERE instrument_id = ? "
                      "ORDER BY published_at DESC", [instrument_id])
    return [(t, datetime.fromtimestamp(e, timezone.utc)) for t, e in rows]


def main(argv: list[str] | None = None, lake_path: str = "data/lake") -> None:
    from smt.data.members import symbol_to_isin

    p = argparse.ArgumentParser(prog="smt.thesis")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set")
    s.add_argument("symbol")
    s.add_argument("text")
    sh = sub.add_parser("show")
    sh.add_argument("symbol")
    a = p.parse_args(argv)
    lake = Lake(lake_path)
    iid = symbol_to_isin(lake).get(a.symbol.upper())
    if iid is None:
        raise SystemExit(f"{a.symbol}: not in the loaded member lists, so no instrument id")
    if a.cmd == "set":
        ingest(ThesisEntry(iid, a.symbol, a.text), lake)
        print(f"Saved a thesis for {a.symbol.upper()}. Older entries are kept.")
    got = entries(lake, iid)
    if not got:
        print(f"No thesis recorded for {a.symbol.upper()}.")
    for text, when in got:
        print(f"{when:%Y-%m-%d %H:%M} UTC: {text}")


if __name__ == "__main__":
    main()
