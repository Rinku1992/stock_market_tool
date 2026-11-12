"""Point-in-time price reads for analysts."""

from bisect import bisect_right
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
        dates, bars, pubs = cache[instrument_id]
        k, limit = bisect_right(dates, as_of), cutoff.timestamp()
        return [b for b, p in zip(bars[:k], pubs[:k]) if p <= limit]
    if not lake.has("prices_daily"):
        return []
    rows = lake.query(
        f"""SELECT trade_date, close, volume FROM {lake.read_sql('prices_daily')}
            WHERE instrument_id = ? AND trade_date <= ? AND published_at <= ? ORDER BY trade_date""",
        [instrument_id, as_of, cutoff],
    )
    return [Bar(*r) for r in rows]


def cache_bars(lake: Lake, instrument_ids: list[str]) -> None:
    """Load all bars for these instruments into memory once. load_bars still filters point-in-time.

    The cached instruments (minus indices) also become the universe that factors rank against.
    """
    cache: dict[str, tuple[list[date], list[Bar], list[float]]] = {i: ([], [], []) for i in instrument_ids}
    if lake.has("prices_daily") and instrument_ids:
        marks = ", ".join("?" for _ in instrument_ids)
        rows = lake.query(
            f"""SELECT instrument_id, trade_date, close, volume, epoch(published_at)
                FROM {lake.read_sql('prices_daily')} WHERE instrument_id IN ({marks})
                ORDER BY instrument_id, trade_date""",
            list(instrument_ids),
        )
        for iid, d, close, volume, published in rows:
            dates, bars, pubs = cache[iid]
            dates.append(d)
            bars.append(Bar(d, close, volume))
            pubs.append(published)
    lake._bar_cache = cache
    lake._factor_memo = {}


def universe_ids(lake: Lake) -> list[str]:
    """Equities to rank against: the cached ones, or else every equity with stored prices."""
    if getattr(lake, "_bar_cache", None) is None:
        ids = []
        if lake.has("prices_daily"):
            ids = [r[0] for r in lake.query(
                f"SELECT DISTINCT instrument_id FROM {lake.read_sql('prices_daily')} "
                "WHERE instrument_id NOT LIKE 'IDX:%' ORDER BY 1")]
        cache_bars(lake, ids)
    return sorted(k for k in lake._bar_cache if not k.startswith("IDX:"))
