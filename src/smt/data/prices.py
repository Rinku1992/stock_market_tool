"""Point-in-time price reads for analysts."""

from dataclasses import dataclass
from datetime import date, datetime, timezone

from smt.data.lake import Lake


@dataclass(frozen=True)
class Bar:
    d: date
    close: float
    volume: int


def load_bars(lake: Lake | None, instrument_id: str | None, as_of: date) -> list[Bar]:
    """Daily bars up to as_of, using only rows that were already published on that date."""
    if lake is None or not instrument_id:
        return []
    cutoff = datetime(as_of.year, as_of.month, as_of.day, 23, 59, 59, tzinfo=timezone.utc)
    cache = getattr(lake, "_bar_cache", None)
    if cache is not None and instrument_id in cache:
        limit = cutoff.timestamp()
        return [b for b, published in cache[instrument_id] if b.d <= as_of and published <= limit]
    if not lake.has("prices_daily"):
        return []
    rows = lake.query(
        f"""SELECT trade_date, close, volume FROM {lake.read_sql('prices_daily')}
            WHERE instrument_id = ? AND trade_date <= ? AND published_at <= ? ORDER BY trade_date""",
        [instrument_id, as_of, cutoff],
    )
    return [Bar(*r) for r in rows]


def cache_bars(lake: Lake, instrument_ids: list[str]) -> None:
    """Load all bars for these instruments into memory once. load_bars still filters point-in-time."""
    cache: dict[str, list[tuple[Bar, float]]] = {i: [] for i in instrument_ids}
    if lake.has("prices_daily"):
        marks = ", ".join("?" for _ in instrument_ids)
        rows = lake.query(
            f"""SELECT instrument_id, trade_date, close, volume, epoch(published_at)
                FROM {lake.read_sql('prices_daily')} WHERE instrument_id IN ({marks})
                ORDER BY instrument_id, trade_date""",
            list(instrument_ids),
        )
        for iid, d, close, volume, published in rows:
            cache[iid].append((Bar(d, close, volume), published))
    lake._bar_cache = cache
