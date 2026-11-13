"""Feature store on DuckDB.

Every feature carries `known_at` (when the world could first see it) and
`origin` ('code' or 'llm'). Reads are point-in-time: you only get what was
known on the date you ask about. This is what keeps backtests honest.
"""

import threading
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

from smt.schema import AnalystReport, DeskReport

_ORIGINS = {"code", "llm"}

_DDL = """
CREATE TABLE IF NOT EXISTS features (
    symbol VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    value DOUBLE,
    text_value VARCHAR,
    period_end DATE,
    known_at TIMESTAMP NOT NULL,
    origin VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    inserted_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
    run_id VARCHAR NOT NULL,
    analyst_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    as_of DATE NOT NULL,
    body VARCHAR NOT NULL,
    inserted_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS desk_reports (
    run_id VARCHAR NOT NULL,
    unit VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    as_of DATE NOT NULL,
    body VARCHAR NOT NULL,
    inserted_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_calls (
    call_id VARCHAR NOT NULL,
    task VARCHAR NOT NULL,
    model VARCHAR NOT NULL,
    system VARCHAR,
    prompt VARCHAR NOT NULL,
    response VARCHAR NOT NULL,
    called_at TIMESTAMP NOT NULL
);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_ts(d: date | datetime) -> datetime:
    if isinstance(d, datetime):
        return d.replace(tzinfo=None)
    # a plain date means "end of that day"
    return datetime(d.year, d.month, d.day, 23, 59, 59)


class FeatureStore:
    def __init__(self, path: str = ":memory:") -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(path)
        self._lock = threading.Lock()
        with self._lock:
            self._con.execute(_DDL)

    def put_feature(
        self,
        *,
        symbol: str,
        name: str,
        known_at: date | datetime,
        origin: str,
        source: str,
        value: float | None = None,
        text_value: str | None = None,
        period_end: date | None = None,
    ) -> None:
        if origin not in _ORIGINS:
            raise ValueError(f"origin must be one of {_ORIGINS}")
        if value is None and text_value is None:
            raise ValueError("give value or text_value")
        with self._lock:
            self._con.execute(
                "INSERT INTO features VALUES (?,?,?,?,?,?,?,?,?)",
                [symbol, name, value, text_value, period_end, _to_ts(known_at), origin, source, _now()],
            )

    def get_feature_asof(self, symbol: str, name: str, as_of: date | datetime) -> dict | None:
        """Latest value of a feature that was already known at `as_of`."""
        with self._lock:
            cur = self._con.execute(
                """SELECT symbol, name, value, text_value, period_end, known_at, origin, source
                   FROM features
                   WHERE symbol = ? AND name = ? AND known_at <= ?
                   ORDER BY known_at DESC, inserted_at DESC LIMIT 1""",
                [symbol, name, _to_ts(as_of)],
            )
            row = cur.fetchone()
            cols = [c[0] for c in cur.description]
        return dict(zip(cols, row)) if row else None

    def save_report(self, report: AnalystReport, run_id: str) -> None:
        with self._lock:
            self._con.execute(
                "INSERT INTO reports VALUES (?,?,?,?,?,?)",
                [run_id, report.analyst_id, report.symbol, report.as_of, report.model_dump_json(), _now()],
            )

    def count_reports(self, run_id: str) -> int:
        with self._lock:
            return self._con.execute("SELECT count(*) FROM reports WHERE run_id = ?", [run_id]).fetchone()[0]

    def save_desk_report(self, report: DeskReport, run_id: str) -> None:
        with self._lock:
            self._con.execute(
                "INSERT INTO desk_reports VALUES (?,?,?,?,?,?)",
                [run_id, report.unit, report.symbol, report.as_of, report.model_dump_json(), _now()],
            )

    def count_desk_reports(self, run_id: str) -> int:
        with self._lock:
            return self._con.execute("SELECT count(*) FROM desk_reports WHERE run_id = ?", [run_id]).fetchone()[0]

    def log_llm_call(self, *, task: str, model: str, system: str | None, prompt: str, response: str) -> str:
        """The recorder. Every LLM output is saved with a timestamp."""
        call_id = uuid.uuid4().hex
        with self._lock:
            self._con.execute(
                "INSERT INTO llm_calls VALUES (?,?,?,?,?,?,?)",
                [call_id, task, model, system, prompt, response, _now()],
            )
        return call_id

    def count_llm_calls(self) -> int:
        with self._lock:
            return self._con.execute("SELECT count(*) FROM llm_calls").fetchone()[0]
