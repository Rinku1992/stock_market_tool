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
- **prices_daily v2: added `price_basis` (adjusted / as_traded).** Live check on RELIANCE
  showed Kite day candles are already adjusted for bonus, so the first idea (store as-traded
  and adjust ourselves) does not fit Kite. Adjusted prices are right for returns and trend
  work. Known cost: Kite rewrites past prices after each new split or bonus, so that stock's
  full history must be re-pulled then. Valuation at a past date must use per-share numbers
  on the same basis.
- **Price analysts give views from simple written rules, with confidence capped at 0.3.**
  The rules (moving averages plus return versus Nifty 50) are not backtested yet. The cap
  and a note on every report keep them from being over-trusted. The verdict stays
  "no call" until the scoring engine and backtest exist.
- **Liquidity and market beta report facts only, with neutral views.** They are context
  and constraints, not buy or sell signals.
- **Price trend gives no view for the 2-year horizon.** Trend alone says little that far out.
- **Data policy widened (owner's call).** Allowed: paid licensed APIs, official public sources,
  and other sites whose terms allow automated access. Not allowed: getting around logins or
  anti-bot blocks, unofficial API wrappers. This replaces the earlier "paid only" rule.
- **Backtest scores a view by return versus Nifty 50**, after 84 / 189 / 500 trading days for the
  short / mid / long horizon. Evaluated monthly. Raw return would reward any view in a rising
  market, so excess return is the fair test.
- **Backtest shows an ALL row as the baseline, and flags cells with fewer than 30 cases.**
  A view is only useful if it beats the baseline, and small samples must not be trusted.
- **Known gaps in the backtest:** no dividends, no trading costs, and the stock list is chosen
  today (survivorship bias) until index membership history is loaded.
- **Parquet file names include what the request was for, not only the raw payload hash.**
  Bug found by a test: two instruments that returned byte-identical payloads overwrote each
  other's file. Real data loaded so far was not affected (RELIANCE and NIFTY 50 payloads differ).
- **Move from single-stock rules to ranked factors.** Professional systems rank many stocks
  on factors (momentum, value, quality, volatility) rather than forecast one price. Price
  forecasting models (ARIMA, LSTM and similar) are not used for direction: on price alone they
  tend to fit noise. SMA rule stays as a baseline the factors must beat.
- **Momentum = 12-month return skipping the last month, divided by 1-year volatility.** The skip
  avoids short-term reversal. Dividing by volatility favours steady risers. One fixed definition,
  chosen before seeing results, to avoid tuning on the past.
- **Momentum views by tercile:** top third positive, bottom third negative. Needs at least 10
  stocks to rank. Stocks with under 13 months of history, or no price in the last 10 days, are
  left out of the ranking.
- **Backtest now shows the median as well as the mean**, so a few big moves cannot hide.
- **No automated access to nseindia.com.** Whether NSE allows it is not verified (robots.txt
  could not be read; a forum answer says the site generally does not allow scraping). Files from
  NSE are downloaded by hand by the owner and loaded through adapters that read local files.
- **Index member lists are matched by column name (symbol, ISIN, series).** The exact NSE file
  layout is not verified, so the parser fails loudly if the columns are missing.
- **Backtest on today's index list has survivorship bias.** Stocks that left the index are
  missing. The output says so until membership history is loaded.
- **Backtest benchmark defaults to the median of all stocks in the run (10+ stocks).** On the
  Nifty 100, the whole list beat the Nifty 50 (survivorship bias, strong Next 50), so beating the
  Nifty 50 said little about a rule. Against the same-date median, a rule must pick better than
  its own universe. `--vs nifty` keeps the old test.
- **Price signals are context, not verdict inputs (13 Nov 2026).** Against the median Nifty 100
  stock, neither the SMA rule nor momentum separates winners from losers at the median (gaps of
  0.2 to 1.2 points, hit rates about 50%). Momentum's higher mean comes from a few big winners.
  Both analysts still run and report facts, with confidence capped, but the scoring engine will
  not use them until a test shows an edge.
- **Earlier rule, now replaced: paid, licensed data only (owner's call).** No free scrapers or unofficial wrappers.
  Scraped data breaks without notice and has no point-in-time history, which goes against
  the reliability benchmark. Vendor not chosen yet.
