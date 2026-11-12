# Decisions log

Newest at the bottom. One entry per choice, with the reason.

## 2026-11-12

- **LangGraph for the orchestrator only.** It gives parallel runs, retries, timeouts and
  resume, so we do not write that glue. Analysts stay plain functions so they are easy to
  test and we are not locked in.
- **Fixed graph, no free-roaming agents.** Repeatable output is the reliability benchmark.
- **Own thin Claude wrapper, no LangChain chains.** Prompts stay visible, every call is
  recorded, and a second LLM can be added later for cross-checks.
- **DuckDB first, BigQuery later.** Free and fast on a laptop. SQL carries over.
- **Point-in-time feature store from day one.** Each feature stores when it became known.
  Without this, backtests leak the future.
- **`origin` tag on every feature (code or llm).** LLM features on dates before the model
  cutoff can leak the future, so backtests must be able to exclude them.
- **Report schema rejects directional views without evidence, and evidence dated after
  `as_of`.** The rule is enforced in code, not left to prompts.
- **Data grade C stops the run.** No verdict on bad data.
- **Defaults:** laptop, Nifty 100.
- **Two storage layers: raw (untouched) and canonical (Parquet).** Raw lets us re-parse
  after any parser or schema change. Parquet is portable to BigQuery and cloud storage.
- **Our own data contract with a validation gate.** Vendors map into our schema through
  adapters, so changing vendor changes one file. Details in docs/DATA_CONTRACT.md.
- **ISIN as instrument id, prices stored unadjusted, financials in tall format, rows
  append-only.** Each choice removes a known cause of data rework.
- **Paid, licensed data only (owner's call).** No free scrapers or unofficial wrappers.
  Scraped data breaks without notice and has no point-in-time history, which goes against
  the reliability benchmark. Vendor not chosen yet.
