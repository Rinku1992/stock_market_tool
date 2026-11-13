"""Reads of stored index membership."""

from smt.data.lake import Lake


def index_members(lake: Lake, index_id: str) -> list[tuple[str, str | None]]:
    """(ISIN, symbol) of the most recently stored member list of an index."""
    if not lake.has("index_members"):
        return []
    src = lake.read_sql("index_members")
    return lake.query(
        f"""SELECT instrument_id, source_ref FROM {src}
            WHERE index_id = ? AND valid_from = (SELECT max(valid_from) FROM {src} WHERE index_id = ?)
            ORDER BY source_ref""",
        [index_id, index_id],
    )


def symbol_to_isin(lake: Lake) -> dict[str, str]:
    """Symbol -> ISIN from your stored holdings and the Nifty 100 list."""
    from smt.data.holdings import latest_holdings

    known = {}
    for isin, sym in latest_holdings(lake) + index_members(lake, "IDX:NIFTY100"):
        if sym:
            known[sym.upper()] = isin
    return known
