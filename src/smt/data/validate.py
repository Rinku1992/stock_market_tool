"""Gate between adapters and storage. Nothing enters the canonical store without passing here."""

import pyarrow as pa
import pyarrow.compute as pc

from smt.data.contract import Dataset


class ContractError(ValueError):
    pass


def _all(mask) -> bool:
    return bool(pc.all(mask, skip_nulls=True, min_count=0).as_py())


def conform(table: pa.Table, ds: Dataset) -> pa.Table:
    """Return the table cast to the dataset schema, or raise ContractError."""
    want = ds.schema
    missing = set(want.names) - set(table.column_names)
    extra = set(table.column_names) - set(want.names)
    if missing:
        raise ContractError(f"{ds.name}: missing columns {sorted(missing)}")
    if extra:
        raise ContractError(f"{ds.name}: unknown columns {sorted(extra)}")

    for field in want:
        if not field.nullable and table[field.name].null_count:
            raise ContractError(f"{ds.name}: nulls in required column '{field.name}'")
    try:
        table = table.select(want.names).cast(want)
    except (pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowNotImplementedError) as e:
        raise ContractError(f"{ds.name}: type mismatch: {e}") from e

    if table.num_rows == 0:
        return table

    if any(v != ds.version for v in pc.unique(table["schema_version"]).to_pylist()):
        raise ContractError(f"{ds.name}: schema_version must be {ds.version}")

    n_keys = table.group_by(list(ds.key)).aggregate([([], "count_all")]).num_rows
    if n_keys != table.num_rows:
        raise ContractError(f"{ds.name}: duplicate rows for key {ds.key}")

    if not _all(pc.less_equal(table["published_at"], table["ingested_at"])):
        raise ContractError(f"{ds.name}: published_at is after ingested_at")

    for col, allowed in ds.enums:
        # an empty value in an optional column is allowed; required columns were checked for nulls above
        if not _all(pc.or_(pc.is_null(table[col]), pc.is_in(table[col], value_set=pa.array(sorted(allowed))))):
            raise ContractError(f"{ds.name}: '{col}' has a value outside {sorted(allowed)}")

    _CHECKS.get(ds.name, lambda t: None)(table)
    return table


def _check_prices(t: pa.Table) -> None:
    for c in ("open", "high", "low", "close"):
        if not _all(pc.greater(t[c], 0)):
            raise ContractError(f"prices_daily: '{c}' must be > 0")
    ok = pc.and_(
        pc.and_(pc.less_equal(t["low"], t["open"]), pc.less_equal(t["low"], t["close"])),
        pc.and_(pc.greater_equal(t["high"], t["open"]), pc.greater_equal(t["high"], t["close"])),
    )
    if not _all(ok):
        raise ContractError("prices_daily: open/close outside the low-high range")
    if not _all(pc.greater_equal(t["volume"], 0)):
        raise ContractError("prices_daily: negative volume")


def _check_pct(t: pa.Table) -> None:
    for c in ("pct", "pledged_pct"):
        if not _all(pc.and_(pc.greater_equal(t[c], 0), pc.less_equal(t[c], 100))):
            raise ContractError(f"shareholding: '{c}' must be between 0 and 100")


_CHECKS = {"prices_daily": _check_prices, "shareholding": _check_pct}
