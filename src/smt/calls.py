"""Call logger (Track record office). Plain code: no LLM, no LangGraph.

Records what the engine says, so it can be scored later against what really happened.
The records are only worth anything if they were made BEFORE the outcome was known, so:

- No backfill, ever. Calls are recorded only for today's date in India (IST), and only after
  the market close (15:30 IST). Any other date is refused.
- Prices must be current: the Nifty 50 must have a bar for today (so today was a trading day and
  prices were updated), and every stock must have one too. Otherwise nothing is recorded.
- Recording the same day twice adds nothing: stocks already recorded that day are skipped.

Rows go through an adapter and the data contract like every other dataset (raw JSON kept, then
validated Parquet in the "calls" dataset).
"""

import json
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from smt.analysts.market_activity import BENCHMARK
from smt.data.adapter import ingest
from smt.data.lake import Lake
from smt.data.prices import load_bars

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc
MARKET_CLOSE_IST = time(15, 30)
_REPO = Path(__file__).resolve().parents[2]
_VERDICT_DIRECTION = {"consider": "positive", "avoid": "negative", "no_call": "neutral"}


class RecordRefused(RuntimeError):
    """Recording would break the rules above. The message says why."""


def check_record_time(as_of: date, now: datetime) -> None:
    """Only today's date (IST), and only after the 15:30 IST close."""
    now_ist = now.astimezone(IST)
    if as_of != now_ist.date():
        raise RecordRefused(
            f"Calls can only be recorded for today ({now_ist.date()}, India time), not {as_of}. "
            "No backfill: a call recorded after the fact could use knowledge of the outcome.")
    if now_ist.time() < MARKET_CLOSE_IST:
        raise RecordRefused(
            f"It is {now_ist:%H:%M} India time. Calls can only be recorded after the market close at 15:30, "
            "when the day's prices are final.")


def stale_prices(lake: Lake, instrument_ids: list[str], as_of: date) -> list[tuple[str, date | None]]:
    """Stocks whose latest stored bar is not the last trading day. Raises if the index itself is not current.

    The last trading day is taken from the Nifty 50: after update-prices, it has a bar for today if,
    and only if, the market was open today.
    """
    idx = load_bars(lake, BENCHMARK, as_of)
    last = idx[-1].d if idx else None
    if last != as_of:
        raise RecordRefused(
            f"The Nifty 50 has no price for today ({as_of}); its latest is {last or 'none'}. Either the market was "
            "closed today, or prices are not updated yet (run: python -m smt.kite_cli update-prices).")
    stale = []
    for iid in instrument_ids:
        bars = load_bars(lake, iid, as_of)
        latest = bars[-1].d if bars else None
        if latest != as_of:
            stale.append((iid, latest))
    return stale


def code_version() -> str:
    """The git commit the engine ran from, with "-dirty" if tracked files had uncommitted changes."""
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=_REPO, capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "diff", "--quiet", "HEAD"], cwd=_REPO).returncode != 0
        return sha + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def call_rows(state: dict, instrument_id: str, close: float, version: str, recorded_at: datetime) -> list[dict]:
    """One row per source (each analyst, each desk lead, the verdict) per horizon."""
    as_of, symbol = state["as_of"], state["symbol"]
    base = {"as_of": as_of, "instrument_id": instrument_id, "symbol": symbol, "close_price": close,
            "code_version": version, "published_at": recorded_at}
    rows = []
    for r in state.get("reports", []):
        js = r.model_dump_json()
        for v in r.views:
            rows.append({**base, "source_kind": "analyst", "source_id": r.analyst_id, "horizon": v.horizon.value,
                         "direction": v.direction.value, "call": None, "strength": v.strength, "confidence": v.confidence,
                         "weights_version": None, "report_json": js})
    for d in state.get("desk_reports", []):
        js = d.model_dump_json()
        for v in d.views:
            rows.append({**base, "source_kind": "desk", "source_id": d.unit, "horizon": v.horizon.value,
                         "direction": v.direction.value, "call": None, "strength": v.strength, "confidence": v.confidence,
                         "weights_version": d.weights_version, "report_json": js})
    verdict = state.get("verdict")
    if verdict is not None:
        js = verdict.model_dump_json()
        for h in verdict.horizons:
            rows.append({**base, "source_kind": "verdict", "source_id": "verdict", "horizon": h.horizon.value,
                         "direction": _VERDICT_DIRECTION[h.call.value], "call": h.call.value, "strength": None,
                         "confidence": h.confidence, "weights_version": None, "report_json": js})
    return rows


class CallRecord:
    """Adapter for one stock's calls on one day. fetch() serialises the rows; parse() reads them back."""

    name = "call_logger"
    dataset = "calls"
    raw_ext = "json"

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    @property
    def context(self) -> dict:
        r = self.rows[0] if self.rows else {}
        return {"as_of": str(r.get("as_of")), "instrument_id": r.get("instrument_id")}

    def fetch(self) -> bytes:
        return json.dumps(self.rows, default=str, sort_keys=True).encode()

    def parse(self, payload: bytes) -> list[dict]:
        out = []
        for r in json.loads(payload):
            out.append({**r, "as_of": date.fromisoformat(r["as_of"]),
                        "published_at": datetime.fromisoformat(r["published_at"])})
        return out


def recorded_ids(lake: Lake, as_of: date) -> set[str]:
    if not lake.has("calls"):
        return set()
    return {r[0] for r in lake.query(f"SELECT DISTINCT instrument_id FROM {lake.read_sql('calls')} WHERE as_of = ?", [as_of])}


@dataclass
class RecordResult:
    as_of: date
    recorded: list[str] = field(default_factory=list)
    already: list[str] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)  # (symbol, reason): data office said no
    rows: int = 0


def record_day(lake: Lake, store, stocks: list[tuple[str, str | None]], now: datetime | None = None,
               as_of: date | None = None, run_fn=None, version: str | None = None) -> RecordResult:
    """Run the engine for each stock and store its calls. stocks: (ISIN, symbol)."""
    if run_fn is None:
        from smt.graph import run as run_fn
    now = now or datetime.now(UTC)
    as_of = as_of or now.astimezone(IST).date()
    check_record_time(as_of, now)
    stocks = list(dict.fromkeys(stocks))  # same stock in holdings and index: once
    stale = stale_prices(lake, [iid for iid, _ in stocks], as_of)
    if stale:
        names = {iid: sym or iid for iid, sym in stocks}
        listed = ", ".join(f"{names[i]} (latest {d or 'none'})" for i, d in stale)
        raise RecordRefused(f"Prices are not current for {len(stale)} stock(s): {listed}. "
                            "Run: python -m smt.kite_cli update-prices. Nothing was recorded.")
    version = version or code_version()
    result, done = RecordResult(as_of), recorded_ids(lake, as_of)
    for iid, sym in stocks:
        symbol = sym or iid
        if iid in done:
            result.already.append(symbol)
            continue
        state = run_fn(symbol, as_of, store, lake=lake, instrument_id=iid)
        if state["verdict"].refused_reason:
            result.refused.append((symbol, state["verdict"].refused_reason))
            continue
        close = load_bars(lake, iid, as_of)[-1].close
        rows = call_rows(state, iid, close, version, datetime.now(UTC))
        ingest(CallRecord(rows), lake)
        result.recorded.append(symbol)
        result.rows += len(rows)
    return result


def status(lake: Lake) -> list[tuple]:
    """(as_of, stocks, calls, first recorded at, last recorded at, code versions) per day."""
    if not lake.has("calls"):
        return []
    # timestamps come back as epoch seconds: returning a time-zone timestamp would need pytz
    rows = lake.query(
        f"""SELECT as_of, count(DISTINCT instrument_id), count(*), epoch(min(published_at)), epoch(max(published_at)),
                   string_agg(DISTINCT left(code_version, 7) || CASE WHEN code_version LIKE '%-dirty' THEN '-dirty' ELSE '' END, ', ')
            FROM {lake.read_sql('calls')} GROUP BY as_of ORDER BY as_of""")
    return [(d, n, c, datetime.fromtimestamp(a, UTC), datetime.fromtimestamp(b, UTC), v) for d, n, c, a, b, v in rows]
