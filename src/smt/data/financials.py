"""Point-in-time reads of company financials.

A result announced after the market closed on day D can only be traded on from day D+1.
So "known as of D" means published by D's market close (15:30 IST = 10:00 UTC), not by
midnight. Using midnight would let a backtest act on evening results at that day's close.
"""

from datetime import date, datetime, timezone

from smt.data.lake import Lake

UTC = timezone.utc


def market_close_utc(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 10, 0, tzinfo=UTC)


def load_financials(lake: Lake | None, instrument_id: str | None, as_of: date, consolidated: bool | None = None) -> list[dict]:
    """Latest known value of every line item, as of the market close of as_of.

    If the same figure was published twice (restated), the version known on as_of wins.
    """
    if lake is None or not instrument_id or not lake.has("financials"):
        return []
    cond, params = "", [instrument_id, market_close_utc(as_of)]
    if consolidated is not None:
        cond, params = " AND consolidated = ?", params + [consolidated]
    rows = lake.query(
        f"""SELECT period_end, period_type, statement, consolidated, line_item, value, epoch(published_at) FROM (
                SELECT *, row_number() OVER (PARTITION BY period_end, period_type, statement, consolidated, line_item
                                             ORDER BY published_at DESC) AS rn
                FROM {lake.read_sql('financials')}
                WHERE instrument_id = ? AND published_at <= ?{cond})
            WHERE rn = 1 ORDER BY period_end""",
        params,
    )
    cols = ("period_end", "period_type", "statement", "consolidated", "line_item", "value")
    # published_at comes back as epoch seconds: returning a time-zone timestamp would need pytz
    return [{**dict(zip(cols, r[:6])), "published_at": datetime.fromtimestamp(r[6], UTC)} for r in rows]
