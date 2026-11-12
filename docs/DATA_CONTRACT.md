# Data contract

Our own schema for every dataset. Vendors change; this does not.
Code: `src/smt/data/contract.py`. This page explains the choices.

## Flow

    source --fetch--> raw/ (untouched, gzip) --parse--> validate --> canonical/ (Parquet) --> feature store

## Format choices and why

| Choice | Why it avoids trouble later |
|---|---|
| Raw payload kept forever, unchanged | A parser bug or schema change is fixed by re-parsing. No re-buying or re-fetching data |
| Canonical data in Parquet (zstd) | Typed, compressed, and read as-is by DuckDB, BigQuery, pandas, Spark. Moving to cloud is a file copy |
| One folder per dataset / schema version / year | Old and new schema versions can sit side by side. Loads stay fast as history grows |
| Append-only rows | Nothing is overwritten. Restated figures are new rows, so history is never lost |
| `published_at` on every row | Backtests only see what was known on that date |
| ISIN as the id, not the symbol | Symbols get renamed. `IDX:<NAME>` is used for indices |
| INR in absolute rupees, timestamps in UTC | No lakh/crore or time zone mix-ups between vendors |
| Prices stored as traded, not adjusted | Vendors adjust differently. We compute adjustment ourselves from corporate actions |
| Financials in tall format (one row per line item) | A new line item is a new row, not a schema change |
| fetch and parse are separate steps | Parsing can be re-run offline and unit tested without a network |
| Validation gate before storage | Bad vendor data is rejected and never mixes with good data |

## Lineage columns (on every row)

| Column | Meaning |
|---|---|
| `source` | Adapter name |
| `source_ref` | Vendor record id or URL, if known |
| `published_at` | When the world could first see this value |
| `ingested_at` | When we stored it |
| `raw_sha256` | The raw payload this row came from |
| `schema_version` | Version of this dataset's schema |

## Datasets in the contract today

| Dataset | Key | Notes |
|---|---|---|
| `instruments` | instrument_id, exchange, valid_from | Symbol history |
| `prices_daily` | instrument_id, exchange, trade_date | Equities and indices, unadjusted |
| `corporate_actions` | instrument_id, ex_date, action_type | Split, bonus, dividend, buyback, rights, demerger, merger |
| `financials` | instrument_id, period_end, period_type, statement, consolidated, line_item, published_at | Tall format |
| `shareholding` | instrument_id, period_end, holder_category, published_at | Includes pledge |
| `index_members` | index_id, instrument_id, valid_from | Membership over time |
| `holdings` | account, instrument_id, as_of | `account` is an alias, never the broker client id |
| `documents` | doc_id | Index of stored PDFs and transcripts |

Still to add, each when its desk is built: news, announcements, insider and bulk/block deals,
F&O, credit ratings, surveillance lists, macro series, event calendar, peer mapping.

## Rules for changing a schema

1. Adding a nullable column: allowed, bump `version`.
2. Renaming, removing or retyping a column: new `version`, written to a new `v<N>` folder.
   Old data is rebuilt from raw with `reparse`.
3. New enum value (for example a new action type): add it to the set in `contract.py`.
4. Line item names in `financials` are our own canonical names. Each adapter maps the
   vendor's names to ours. The canonical list is set when the first fundamentals vendor is chosen.

## Adding a source

Write one class with `name`, `dataset`, `raw_ext`, `fetch()` and `parse()`.
`ingest(adapter, lake)` does the rest. See `tests/test_data_contract.py` for an example.
