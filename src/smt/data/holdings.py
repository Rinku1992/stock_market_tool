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
