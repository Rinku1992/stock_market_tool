"""The data contract: our own schema for each dataset, independent of any vendor.

Conventions (apply to every dataset):
- instrument_id: ISIN for equities, "IDX:<NAME>" for indices. Symbols change, ISINs rarely do.
- Dates are calendar dates. Timestamps are UTC.
- Money is in INR, absolute rupees. Never lakhs or crores.
- Rows are append-only. A restatement is a new row with a later published_at.
- Every row carries lineage: where it came from and when the world could first see it.
"""

from dataclasses import dataclass

import pyarrow as pa

TS = pa.timestamp("us", tz="UTC")


def _f(name: str, typ: pa.DataType, required: bool = True) -> pa.Field:
    return pa.field(name, typ, nullable=not required)


LINEAGE = [
    _f("source", pa.string()),  # adapter name, e.g. "kite"
    _f("source_ref", pa.string(), required=False),  # vendor record id or URL
    _f("published_at", TS),  # when the world could first see this value
    _f("ingested_at", TS),  # when we stored it
    _f("raw_sha256", pa.string()),  # the raw payload this row was parsed from
    _f("schema_version", pa.int32()),
]


@dataclass(frozen=True)
class Dataset:
    name: str
    version: int
    fields: tuple[pa.Field, ...]
    key: tuple[str, ...]
    partition_by: str | None = None  # a date column; files are split by its year
    enums: tuple[tuple[str, frozenset[str]], ...] = ()

    @property
    def schema(self) -> pa.Schema:
        return pa.schema(list(self.fields) + LINEAGE)


INSTRUMENT_TYPES = frozenset({"equity", "index"})
# "adjusted": the source rewrote past prices for splits/bonus (good for returns, but old values
# change after each new corporate action). "as_traded": the price that was really printed that day.
PRICE_BASES = frozenset({"adjusted", "as_traded"})
ACTION_TYPES = frozenset({"split", "bonus", "dividend", "buyback", "rights", "demerger", "merger", "other"})
# Q quarter, H half year to date, N nine months to date, A full year, I balance-sheet date (an instant)
PERIOD_TYPES = frozenset({"Q", "H", "N", "A", "I"})
STATEMENTS = frozenset({"pl", "bs", "cf"})
HOLDER_CATEGORIES = frozenset({"promoter", "fii", "dii", "public", "other"})
DOC_TYPES = frozenset(
    {"annual_report", "quarterly_result", "concall_transcript", "investor_presentation", "rating_note", "announcement", "other"}
)

INSTRUMENTS = Dataset(
    name="instruments",
    version=1,
    fields=(
        _f("instrument_id", pa.string()),
        _f("symbol", pa.string()),
        _f("exchange", pa.string()),
        _f("name", pa.string()),
        _f("instrument_type", pa.string()),
        _f("valid_from", pa.date32()),
        _f("valid_to", pa.date32(), required=False),  # null = still valid
    ),
    key=("instrument_id", "exchange", "valid_from"),
    enums=(("instrument_type", INSTRUMENT_TYPES),),
)

PRICES_DAILY = Dataset(
    name="prices_daily",
    version=2,
    fields=(
        _f("instrument_id", pa.string()),
        _f("exchange", pa.string()),
        _f("trade_date", pa.date32()),
        # Whether these prices were adjusted by the source for splits/bonus. See PRICE_BASES.
        _f("price_basis", pa.string()),
        _f("open", pa.float64()),
        _f("high", pa.float64()),
        _f("low", pa.float64()),
        _f("close", pa.float64()),
        _f("volume", pa.int64()),
    ),
    key=("instrument_id", "exchange", "trade_date"),
    partition_by="trade_date",
    enums=(("price_basis", PRICE_BASES),),
)

CORPORATE_ACTIONS = Dataset(
    name="corporate_actions",
    version=1,
    fields=(
        _f("instrument_id", pa.string()),
        _f("ex_date", pa.date32()),
        _f("action_type", pa.string()),
        _f("ratio_num", pa.float64(), required=False),  # split 1:5 -> num=5, den=1 (new:old)
        _f("ratio_den", pa.float64(), required=False),
        _f("amount", pa.float64(), required=False),  # INR per share, for dividends
        _f("details", pa.string(), required=False),
    ),
    key=("instrument_id", "ex_date", "action_type"),
    partition_by="ex_date",
    enums=(("action_type", ACTION_TYPES),),
)

# Tall format on purpose: one row per line item. New line items never change the schema.
FINANCIALS = Dataset(
    name="financials",
    version=1,
    fields=(
        _f("instrument_id", pa.string()),
        _f("period_end", pa.date32()),
        _f("period_type", pa.string()),
        _f("statement", pa.string()),
        _f("consolidated", pa.bool_()),
        _f("line_item", pa.string()),  # our canonical name, see docs/DATA_CONTRACT.md
        _f("value", pa.float64()),  # INR, absolute
    ),
    # published_at is in the key so first-reported and restated values both stay.
    key=("instrument_id", "period_end", "period_type", "statement", "consolidated", "line_item", "published_at"),
    partition_by="period_end",
    enums=(("period_type", PERIOD_TYPES), ("statement", STATEMENTS)),
)

SHAREHOLDING = Dataset(
    name="shareholding",
    version=1,
    fields=(
        _f("instrument_id", pa.string()),
        _f("period_end", pa.date32()),
        _f("holder_category", pa.string()),
        _f("pct", pa.float64()),  # 0-100, share of total equity
        _f("pledged_pct", pa.float64(), required=False),  # 0-100, share of this holder's stake
    ),
    key=("instrument_id", "period_end", "holder_category", "published_at"),
    partition_by="period_end",
    enums=(("holder_category", HOLDER_CATEGORIES),),
)

INDEX_MEMBERS = Dataset(
    name="index_members",
    version=1,
    fields=(
        _f("index_id", pa.string()),
        _f("instrument_id", pa.string()),
        _f("valid_from", pa.date32()),
        _f("valid_to", pa.date32(), required=False),
    ),
    key=("index_id", "instrument_id", "valid_from"),
)

HOLDINGS = Dataset(
    name="holdings",
    version=1,
    fields=(
        _f("account", pa.string()),  # an alias you choose, never the broker client id
        _f("instrument_id", pa.string()),
        _f("as_of", pa.date32()),
        _f("quantity", pa.float64()),
        _f("avg_price", pa.float64()),
    ),
    key=("account", "instrument_id", "as_of"),
    partition_by="as_of",
)

DOCUMENTS = Dataset(
    name="documents",
    version=1,
    fields=(
        _f("doc_id", pa.string()),  # sha256 of the file
        _f("instrument_id", pa.string()),
        _f("doc_type", pa.string()),
        _f("title", pa.string(), required=False),
        _f("url", pa.string(), required=False),
        _f("file_path", pa.string()),  # path of the stored original, relative to the lake root
        _f("mime", pa.string()),
    ),
    key=("doc_id",),
    enums=(("doc_type", DOC_TYPES),),
)

CALL_SOURCE_KINDS = frozenset({"analyst", "desk", "verdict"})
HORIZONS = frozenset({"short", "mid", "long"})
DIRECTIONS = frozenset({"positive", "negative", "neutral"})
VERDICT_CALLS = frozenset({"consider", "avoid", "no_call"})

# What the engine said, recorded on the day it said it (after market close, never backfilled),
# so it can be scored later against what really happened. published_at = when it was recorded.
CALLS = Dataset(
    name="calls",
    version=1,
    fields=(
        _f("as_of", pa.date32()),  # the IST trading day the call was made on
        _f("instrument_id", pa.string()),
        _f("symbol", pa.string()),
        _f("source_kind", pa.string()),  # analyst / desk / verdict
        _f("source_id", pa.string()),  # analyst id, desk unit, or "verdict"
        _f("horizon", pa.string()),
        _f("direction", pa.string()),  # for a verdict: consider -> positive, avoid -> negative, no_call -> neutral
        _f("call", pa.string(), required=False),  # verdict only: the exact call
        _f("strength", pa.float64(), required=False),  # verdicts have none
        _f("confidence", pa.float64()),
        _f("close_price", pa.float64()),  # the stock's close on as_of, INR
        _f("weights_version", pa.string(), required=False),  # desk rows only
        _f("code_version", pa.string()),  # git commit, "-dirty" if the code had uncommitted changes
        _f("report_json", pa.string()),  # the full report the row came from
    ),
    key=("as_of", "instrument_id", "source_kind", "source_id", "horizon"),
    partition_by="as_of",
    enums=(("source_kind", CALL_SOURCE_KINDS), ("horizon", HORIZONS), ("direction", DIRECTIONS), ("call", VERDICT_CALLS)),
)

DATASETS: dict[str, Dataset] = {
    d.name: d
    for d in (INSTRUMENTS, PRICES_DAILY, CORPORATE_ACTIONS, FINANCIALS, SHAREHOLDING, INDEX_MEMBERS, HOLDINGS, DOCUMENTS, CALLS)
}
