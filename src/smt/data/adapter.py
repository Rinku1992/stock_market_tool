"""What a data source looks like to us.

fetch() talks to the network and returns the raw payload untouched.
parse() is pure: raw bytes in, contract table out. Keeping them apart means a parser
fix can be re-run over stored raw files without calling the vendor again.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import pyarrow as pa

from smt.data.contract import DATASETS
from smt.data.lake import Lake


class SourceAdapter(Protocol):
    name: str  # e.g. "kite"
    dataset: str  # a key of contract.DATASETS
    raw_ext: str  # "json", "csv", ...

    def fetch(self, **params) -> bytes: ...

    def parse(self, payload: bytes) -> list[dict]:
        """Rows with the dataset's own columns plus published_at (and source_ref if known)."""
        ...


def _to_table(adapter: SourceAdapter, rows: list[dict], raw_sha256: str, ingested_at: datetime) -> pa.Table:
    ds = DATASETS[adapter.dataset]
    full = [
        {"source_ref": None, **r, "source": adapter.name, "ingested_at": ingested_at,
         "raw_sha256": raw_sha256, "schema_version": ds.version}
        for r in rows
    ]
    if not full:
        return ds.schema.empty_table()
    return pa.Table.from_pylist(full)


def ingest(adapter: SourceAdapter, lake: Lake, **params) -> list[Path]:
    """fetch -> keep raw -> parse -> validate -> write canonical."""
    now = datetime.now(timezone.utc)
    payload = adapter.fetch(**params)
    # what was asked for goes in the manifest, so a raw file can be understood and re-parsed later
    meta = {**getattr(adapter, "context", {}), **params}
    ref = lake.save_raw(adapter.name, adapter.dataset, payload, adapter.raw_ext, now, meta)
    return lake.write(adapter.dataset, _to_table(adapter, adapter.parse(payload), ref.sha256, now))


def reparse(adapter: SourceAdapter, lake: Lake, raw_path: Path) -> list[Path]:
    """Run the parser again over a stored raw file. No network call."""
    payload = Lake.load_raw(raw_path)
    sha = Path(raw_path).name.split(".")[0]
    return lake.write(adapter.dataset, _to_table(adapter, adapter.parse(payload), sha, datetime.now(timezone.utc)))
