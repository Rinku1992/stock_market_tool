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
- **Desk lead = code decides, LLM explains, code checks the LLM (13 Nov 2026).** The desk view
  per horizon is a weighted mix of its analysts' views. The LLM only writes a summary; code
  rejects it if it holds a number not in the analyst reports, a bullet without an analyst
  citation, or advice words, and then uses a plain summary built by code.
- **The desk learns through versioned weights, not through LLM judgement.** Weights live in
  `src/smt/desks/weights/<unit>.json`, each version naming the test it came from. Market activity
  starts at weight 0 for all four analysts, because none has shown an edge. The planned
  calibration step will re-score analysts on new outcomes (and by market regime) and write new
  versions. An LLM may later propose new rules, but each must pass a backtest and the owner's OK.
- **Company financials come from NSE results XBRL files, downloaded by hand.** Both the older
  "Financial Results" format and the newer "Integrated Filing" format are read. Plain facts are
  stored; segment facts are skipped for now.
- **Older-format trap handled:** year-to-date contexts (ids starting "Four") carry the quarter's
  dates. Their start is set to the financial year start, and two different values for one tag
  and period make the load fail.
- **A filing is "known" from the later of board meeting end and NSE broadcast time, and analysts
  read financials as of that day's market close (15:30 IST).** Results released in the evening
  can only be acted on the next day. Reading as of midnight would leak them into that day's close.
- **financials period_type gains H, N and I** (half year to date, nine months to date, balance-sheet
  date). Allowed contract change: new enum values.
- **financial_trend gives facts only, no direction,** until enough filed history is loaded to test a rule.
- **A missing line item is never treated as zero (13 Nov 2026).** Bug found on M&MFIN: the NBFC
  filing has no "BorrowingsNoncurrent/Current" tags, and the analyst reported debt of Rs 0 crore.
  Now it says the borrowings were not found and lists it as a data gap.
- **Verified against news on 13 Nov 2026:** TITAN Q2 FY27 net profit Rs 2,393 crore vs Rs 1,120 crore
  and M&MFIN consolidated profit Rs 911 crore vs Rs 566 crore match our parsed filings. TITAN's
  jump includes a non-operating fair-value gain that is not tagged as an exceptional item, so
  "exceptional items" alone cannot catch every one-off.
- **Earlier rule, now replaced: paid, licensed data only (owner's call).** No free scrapers or unofficial wrappers.
  Scraped data breaks without notice and has no point-in-time history, which goes against
  the reliability benchmark. Vendor not chosen yet.
- **Stock type router is plain code reading filing tags, decided per stock (13 Nov 2026).** A stock is a
  "lender" if any loaded filing has Loans above zero, or has both InterestEarned above zero and
  ImpairmentOnFinancialInstruments (June and December filings carry no balance sheet, so no Loans).
  The report lists the tags that made the call. No filings means "unknown", not a guess. These tags
  are from the NBFC results taxonomy (checked on M&MFIN); none of the 33 general filings loaded has
  them. Known gap: no bank filing is loaded, so bank tag names are not verified and a bank could
  come out as "general".
- **Lender playbook in financial_trend.** Lenders report total income, net margin (net profit / total
  income), net interest income (interest earned minus finance costs), credit cost and credit cost as a
  share of loans a year (quarter x 4 / loans), loans (total assets if loans are not tagged), each
  borrowing line under its filed name, debt to net worth, and growth versus last year. Ratios we
  compute are labelled "computed by us". EBITDA and the negative operating cash check are skipped:
  they mean nothing for a lender, whose loans count as operating cash outflow.
- **Lender total borrowings = Borrowings (other than debt securities) + DebtSecurities + Deposits +
  SubordinatedLiabilities.** If any of the four is not filed, the found pieces are shown and no total or
  debt to net worth is given. Subordinated debt is included because it is borrowed money.
- **Debt to net worth uses total equity (Equity, including minority interest)**, the same as debt to
  equity for general companies, so the two are comparable (owner's call). Stored as the same
  feature, fin_debt_to_equity.
- **The filing's own DebtEquityRatio and EPS tags are not used; we compute our own.** CORRECTED 13 Nov 2026:
  this entry first said they were 0.0 and looked unfilled. That was wrong: our listing divided every value
  by 1 crore for display, which rounded per-share values and ratios to 0.0. The M&MFIN Sep 2026 filing
  really has basic EPS Rs 6.54 (quarter) and DebtEquityRatio 0.0544. The 0.0544 looks like 5.44 shifted
  two places (we compute 5.43); not verified. Lesson: per-share values and ratios are not in rupees and
  must never be scaled by crore.
- **New canonical items:** loans, borrowings_other_than_debt_securities, debt_securities, deposits,
  subordinated_liabilities, interest_income (InterestEarned), credit_cost (ImpairmentOnFinancialInstruments).
  Files loaded before this change were re-loaded with data_cli so the lake uses the new names.
- **Calibration officer rules, fixed BEFORE running on real data (13 Nov 2026).** Code only, no LLM.
  For each analyst in a desk and each horizon, the existing backtest (scored against the median stock
  on the same date) gives scored views. Then:
  - Edge = median excess return of positive views minus median excess return of negative views.
  - A weight above 0 needs ALL three:
    a. at least 300 positive and 300 negative cases;
    b. stable: the dates are split into 3 periods with equal numbers of dates, in time order, and the
       edge is above 0 in each one (a period with no positive or no negative case fails);
    c. shuffle test: within each date, the views are shuffled among that date's stocks 500 times
       (fixed seed 0, so the result repeats). The real edge must be above at least 95% of the
       shuffled edges. Shuffling within a date keeps each date's mix of views and its market, so
       only the link between view and stock is tested.
  - If all hold: weight = edge / (edge + 0.02), capped at 1. Otherwise weight 0. The 0.02 means a
    2-point edge gets weight 0.5.
  - Market regime (Nifty 50 above or below its 200-day average on the view date) is reported only.
    It does not change weights yet. Dates with under 200 days of index history have no regime.
  - Weights are written only with --write, as a new dated version; the old file is kept in
    desks/weights/history/. Known limit: monthly views overlap in time, so cases are not fully
    independent. The 3-period check is there partly for this.
- **First calibration run, market_activity on the Nifty 100 (13 Nov 2026), not written.** Monthly views
  Feb 2022 to Jun 2026, scored against the median stock. No analyst passes, so every weight stays 0,
  the same as the current file. Momentum 6-12 months: edge +1.2 points overall, but +18.1, -5.2 and
  -1.6 points in the three periods, and it beats only 92% of shuffles, so the whole edge came from one
  early period. Price structure: edge -0.9 points (3-5 months) and 0.0 (6-12 months). Liquidity and
  market beta give no direction. Regime (report only): price structure 3-5 months was +0.3 points
  with the Nifty above its 200-day average and -3.4 points below it. Weights are not written until the
  owner says so.
- **Calibration header and source show scored dates per horizon, from positive and negative views only.**
  The first header said "2022-02-28 to 2026-06-30": the start came from liquidity's neutral views and
  the end was right only for the short horizon. Real spans: short 2022-09-30 to 2026-06-30, mid
  2022-09-30 to 2026-01-30, long none (no directional 2-year views).
- **A second weights version on the same day gets ".2", ".3" ...** so every version name is unique and
  reports can say which weights they used.
- **First calibration run written (owner's OK, 13 Nov 2026):** market_activity weights version
  2026-11-13.2, all 0, with the reasons. The earlier hand-set version is kept in desks/weights/history/.
- **Process slip, 13 Nov 2026:** merge 4b2f82f was pushed with 2 failing tests in test_desk_lead.py (they
  expected the weights version to be exactly "2026-11-13"). The push command did not stop on a pytest
  failure. Fixed in the next commit: the tests now follow the version in the weights file. From now on
  the push runs only after pytest exits cleanly.
- **Call logger: no backfill, ever (13 Nov 2026).** Calls are recorded only for today's date in India and
  only after the 15:30 IST close; any other date is refused. A call written after the fact could use
  knowledge of the outcome, and the track record would be worthless.
- **"Today was a trading day" comes from the Nifty 50 bar.** There is no holiday calendar in the repo.
  After update-prices, the Nifty 50 has a bar for today only if the market was open. No bar for today
  means "closed today or prices not updated", and nothing is recorded. Every stock must also have
  today's bar; if any is stale, the whole run stops and names them.
- **Not verified:** that Kite's day candle fetched just after 15:30 equals NSE's official close. NSE sets
  the close from the last half hour's trades, so a candle pulled a few minutes after 15:30 may differ
  slightly. The close stored with each call is what Kite gave at that time.
- **Same day twice adds nothing:** a stock already recorded that day is skipped. The first record of the
  day stands, so nothing can be quietly replaced after more is known.
- **calls dataset (v1):** one row per stock, source (analyst / desk / verdict) and horizon. Verdict rows
  map consider/avoid/no_call to positive/negative/neutral in `direction` and keep the exact value in a
  `call` column (added beyond the requested columns, so "no call" is not confused with "neutral").
  `strength` is empty for verdicts, `weights_version` is set on desk rows only. Rows go through an
  adapter (raw JSON kept) and the contract like every other dataset.
- **Contract gate: an empty value in an optional enum column is allowed.** Needed for `call`, which is
  empty on analyst and desk rows. Required columns are still checked for empty values first.
- **code_version = git commit, with "-dirty" if tracked files had uncommitted changes.** The first day
  (13 Nov 2026) was recorded as "2e089a5-dirty" because the logger was not committed yet. The analyst,
  desk and graph code was unchanged from 2e089a5; only the logger, contract and update-prices files were new.
- **update-prices pulls the last 10 calendar days** for the Nifty 100 members, your holdings and the
  Nifty 50, up to today if it is after 15:30 IST, else up to yesterday. Ten days covers long weekends
  and holidays. Re-pulled days replace older rows for the same key (newest ingestion wins).
- **record refuses to run with uncommitted changes to tracked files (13 Nov 2026).** Every recorded call
  must point at a commit that can be checked later. Untracked files (data/ and the like) do not count.
  The first day (13 Nov 2026, "2e089a5-dirty") is the only day recorded without this check.
- **close_price in calls is the price seen at record time, not the official close.** The future scorer
  must use prices re-pulled on a later day, not this column, so it gets NSE's official close (see the
  "not verified" note on Kite candles just after 15:30). The column stays as a record of what the
  engine saw.
- **Valuation stage A (13 Nov 2026, owner's spec): docs/VALUATION.md.** Five questions (clean numbers, cash
  earnings, returns on capital, what the price assumes, value range with margin of safety), built in stages
  A-D. Stage A: earnings power, capital returns, relative valuation. Code only, no LLM, all views neutral
  until a backtest supports a rule.
- **12-month figures = the last 4 quarters (month-end quarters).** A missing quarter is named and the
  figure is not computed; it is never filled with zero.
- **12-month cash flow = half year to this September + full year to March - half year to last September.**
  Cash flow is only filed half-yearly. If the latest is a March filing, the full year is used as is.
- **Averages use the balance sheet one year before the closing one (365 days, +/- 10).** If there is none,
  the closing figure is used alone and the report says so.
- **Share count = paid-up capital / face value, checked against share capital on the balance sheet (2%).**
  A June or December filing whose paid-up capital differs by more than 2% from the nearest balance sheet
  is flagged in the report, and the balance sheet figure is used. Real case: GODREJPROP Sep 2026 files
  paid-up capital Rs 5 crore against share capital Rs 151 crore.
- **A share-count jump above 20% is taken as a split or bonus;** only counts from that filing on are used.
  Known gap: Kite prices are split-adjusted while counts come from filings, so a split after the latest
  filing would distort market value until the next filing.
- **Zero means missing for shares, face value, equity, revenue and total assets.** A filed 0 is real for
  dividends and minority interest (the report says "filed as 0").
- **Cash = cash, bank balances and current investments,** labelled that way in every report.
- **EBIT = profit before tax and exceptional items + finance costs - other income** (operating EBIT).
  EBITDA adds depreciation. Negative EBITDA: EV/EBITDA "not meaningful". A loss: no P/E.
- **Real estate: EBIT and EBITDA lead WITH other income, and are also shown without (owner's call,
  13 Nov 2026).** ROIC uses the version with other income. Reason: GODREJPROP's operating EBIT without
  other income is Rs -547 crore while reported profit is positive; other income may include interest from
  joint ventures and project partners (not verified). With it: ROIC 5.4%, EV/EBITDA 32.9x.
- **Lease liabilities are left out** of debt, EV and invested capital: they are not in quarterly filings.
  Stated in each report. Lease-heavy companies (offices, stores) look better on ROIC than they are.
- **Real estate rule:** the Nifty 100 list's industry "Realty", or a filing segment named "Real Estate" or
  "Realty". Inventory share was rejected: TITAN's jewellery stock is 71% of assets, like GODREJPROP's.
  index_members moved to schema v2 with an `industry` column, rebuilt from the stored raw list. Filings
  now also store segment NAMES (dataset filing_segments), not segment values.
- **Lenders skip ROIC, EV, EV/EBITDA, EV/Sales, FCF yield, owner earnings, cash conversion and accruals.**
  Borrowed money is a lender's raw material and lending is operating cash outflow, so these mean nothing.
  P/B leads, next to ROE.
- **Checked against published figures (owner, 13 Nov 2026):** P/B and EV/EBITDA for TCS, M&MFIN and TITAN
  match public sites. TITAN's P/E differs because public sites have not yet rolled in the Sep 2026 quarter;
  ours includes it, so it is more current.
