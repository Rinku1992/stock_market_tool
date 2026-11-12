"""File storage in two layers.

raw/        exactly what the source returned, gzip, never changed. Lets us re-parse forever.
canonical/  validated Parquet in our own schema, split by dataset / schema version / year.

Parquet is read directly by DuckDB, BigQuery, pandas and Spark, so moving to cloud
storage later is a file copy, not a conversion.
"""

import gzip
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from smt.data.contract import DATASETS
from smt.data.validate import conform


@dataclass(frozen=True)
class RawRef:
    sha256: str
    path: Path


class Lake:
    def __init__(self, root: str | Path = "data/lake") -> None:
        self.root = Path(root)
        self.raw = self.root / "raw"
        self.canonical = self.root / "canonical"

    # ---- raw layer -------------------------------------------------------
    def save_raw(self, source: str, dataset: str, payload: bytes, ext: str, fetched_at: datetime | None = None,
                 meta: dict | None = None) -> RawRef:
        fetched_at = fetched_at or datetime.now(timezone.utc)
        sha = hashlib.sha256(payload).hexdigest()
        folder = self.raw / source / dataset / f"{fetched_at:%Y}" / f"{fetched_at:%m}"
        path = folder / f"{sha}.{ext}.gz"
        if not path.exists():  # same bytes are stored once
            folder.mkdir(parents=True, exist_ok=True)
            path.write_bytes(gzip.compress(payload))
            line = {"sha256": sha, "source": source, "dataset": dataset, "path": str(path.relative_to(self.root)),
                    "bytes": len(payload), "fetched_at": fetched_at.isoformat(), "meta": meta or {}}
            with (self.raw / "manifest.jsonl").open("a") as f:
                f.write(json.dumps(line, default=str) + "\n")
        return RawRef(sha, path)

    @staticmethod
    def load_raw(path: Path) -> bytes:
        return gzip.decompress(Path(path).read_bytes())

    # ---- canonical layer -------------------------------------------------
    def _dir(self, dataset: str) -> Path:
        ds = DATASETS[dataset]
        return self.canonical / ds.name / f"v{ds.version}"

    def write(self, dataset: str, table: pa.Table, batch_tag: str = "") -> list[Path]:
        """Validate, then write. Re-parsing the same raw payload replaces its own files.

        batch_tag says what the payload was for (for example which instrument). Two requests can
        return byte-identical payloads, and without the tag one would overwrite the other.
        """
        ds = DATASETS[dataset]
        table = conform(table, ds)
        if table.num_rows == 0:
            return []
        ident = "|".join(sorted(pc.unique(table["raw_sha256"]).to_pylist())) + "#" + batch_tag
        batch = hashlib.sha256(ident.encode()).hexdigest()[:16]
        parts: list[tuple[Path, pa.Table]] = []
        if ds.partition_by:
            years = pc.year(table[ds.partition_by])
            for y in sorted(pc.unique(years).to_pylist()):
                parts.append((self._dir(dataset) / f"year={y}", table.filter(pc.equal(years, y))))
        else:
            parts.append((self._dir(dataset), table))
        out = []
        for folder, part in parts:
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{batch}.parquet"
            pq.write_table(part, path, compression="zstd")
            out.append(path)
        return out

    def has(self, dataset: str) -> bool:
        return any(self._dir(dataset).rglob("*.parquet"))

    def read_sql(self, dataset: str) -> str:
        """A FROM-clause that reads the latest version of every row of a dataset.

        If the same key was ingested twice, the newest ingestion wins.
        """
        ds = DATASETS[dataset]
        glob = (self._dir(dataset) / "**" / "*.parquet").as_posix()
        keys = ", ".join(ds.key)
        return (
            f"(SELECT * EXCLUDE (_rn) FROM (SELECT *, row_number() OVER "
            f"(PARTITION BY {keys} ORDER BY ingested_at DESC) AS _rn "
            f"FROM read_parquet('{glob}', hive_partitioning = false)) WHERE _rn = 1)"
        )

    def query(self, sql: str, params: list | None = None) -> list[tuple]:
        return duckdb.connect().execute(sql, params or []).fetchall()
