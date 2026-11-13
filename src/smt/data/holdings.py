"""Reads of the stored holdings."""

from smt.data.lake import Lake


def latest_holdings(lake: Lake, account: str = "main") -> list[tuple[str, str | None]]:
    """(instrument_id, trading symbol) for the most recent stored holdings of an account."""
    if not lake.has("holdings"):
        return []
    src = lake.read_sql("holdings")
    return lake.query(
        f"""SELECT instrument_id, source_ref FROM {src}
            WHERE account = ? AND as_of = (SELECT max(as_of) FROM {src} WHERE account = ?)
            ORDER BY instrument_id""",
        [account, account],
    )


def top_holdings(lake: Lake, n: int = 10, account: str = "main") -> list[tuple[str, str | None, float]]:
    """(ISIN, symbol, share of total invested) for the n largest holdings by money invested (quantity x buy price)."""
    if not lake.has("holdings"):
        return []
    src = lake.read_sql("holdings")
    rows = lake.query(
        f"""SELECT instrument_id, source_ref, quantity * avg_price AS invested FROM {src}
            WHERE account = ? AND as_of = (SELECT max(as_of) FROM {src} WHERE account = ?)
            ORDER BY invested DESC""",
        [account, account],
    )
    total = sum(r[2] for r in rows) or 1.0
    return [(isin, sym, invested / total) for isin, sym, invested in rows[:n]]


def loaded_quarters(lake: Lake, instrument_id: str) -> list:
    """Quarter-end dates with consolidated results loaded for a stock."""
    if not lake.has("financials"):
        return []
    rows = lake.query(
        f"""SELECT DISTINCT period_end FROM {lake.read_sql('financials')}
            WHERE instrument_id = ? AND consolidated AND period_type = 'Q' AND line_item = 'revenue' ORDER BY 1""",
        [instrument_id],
    )
    return [r[0] for r in rows]
