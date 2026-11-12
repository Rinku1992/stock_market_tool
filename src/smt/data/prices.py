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
    if lake is None or not instrument_id or not lake.has("prices_daily"):
        return []
    cutoff = datetime(as_of.year, as_of.month, as_of.day, 23, 59, 59, tzinfo=timezone.utc)
    rows = lake.query(
        f"""SELECT trade_date, close, volume FROM {lake.read_sql('prices_daily')}
            WHERE instrument_id = ? AND trade_date <= ? AND published_at <= ? ORDER BY trade_date""",
        [instrument_id, as_of, cutoff],
    )
    return [Bar(*r) for r in rows]
