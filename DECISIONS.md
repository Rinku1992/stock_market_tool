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
- **LLM budget guard in llm.py, before any real LLM use (14 Nov 2026).** Every client (Claude and FakeLLM)
  goes through one guard: cache, then estimate, then dry run or caps, then the call, then the tokens from
  the API's usage field are recorded in llm_calls (new columns input_tokens, output_tokens, cache_hit,
  run_id; added to the existing table, older rows keep empty values).
- **Caps count input + output tokens of real calls:** SMT_LLM_MAX_TOKENS_PER_RUN (default 100,000; one
  client = one engine run) and SMT_LLM_MAX_TOKENS_PER_DAY (default 300,000; India day, summed from
  llm_calls so it covers every run). A call that would pass either cap raises LLMBudgetExceeded with the
  tokens used so far; nothing is sent. Never silent.
- **The estimate is deliberately high:** input = characters / 3 + 10 (English is about 4 characters a
  token), output = max_tokens, the most the API can return (thinking counts inside it). So a call is
  stopped before it could pass a cap, not after. Input tokens recorded include any prompt-cache tokens.
- **Cache key = model + system prompt + prompt** (as asked; max_tokens is not part of it). A hit returns
  the stored answer with no API call and is recorded as a row with cache_hit = true and 0 tokens, so hits
  are counted separately and do not use the caps. Only real answers are served from the cache.
- **Dry run (SMT_LLM_DRY_RUN=1)** prints task, model and estimated tokens per call, warns if the run cap
  would be passed, returns an empty answer and records nothing.
- **Model per task:** SMT_CLAUDE_MODEL_<TASK> (task upper-cased, other characters as "_") overrides
  SMT_CLAUDE_MODEL for that task only. No task uses an override yet; the model in use is unchanged.
- **First real call, 14 Nov 2026:** task key_check, one-line prompt, max_tokens 20, model
  claude-sonnet-5-5 (SMT_CLAUDE_MODEL). Answer "ready"; 18 input + 4 output = 22 tokens recorded.

## Valuation stage B, step 1: annual report facts (15 Nov 2026)

- **New dataset report_facts (v1).** The owner's columns plus two: `unit` (as stated: crore, lakh,
  million) and `check_note` (why a row is not verified). Key: instrument_id, fiscal_year_end, item,
  published_at. Values in absolute INR. Not found or failed rows keep value null, never zero.
- **pypdf reads the PDFs** (pure Python, BSD licence). The rupee sign comes out as "H" or "`"; harmless.
- **Candidate pages are picked by code** from keywords. Lease, interest, right-of-use and ESOP items only
  from pages whose running header says consolidated statements (standalone pages are never sent). R&D sits
  in the Board's report, so it may come from any page that also says "consolidated". At most 3 pages and
  12,000 characters per call; items that share pages share one call (one call per report here), and the
  consolidated balance sheet is sent first for current / non-current lease liabilities.
- **The LLM only copies:** item, value as printed, unit, PDF page and an exact quote, as JSON. Code checks:
  quote on that page word for word (spaces and line breaks ignored), printed value is a number in the
  quote, unit stated and on the page, page was sent, year was asked for. Code converts to rupees. Two
  different checked values for one item and year: both rejected.
- **Needs review (owner, 15 Nov 2026):** a quote with more than one number (year or standalone /
  consolidated columns), or a lease liability quote naming both current and non-current, is stored with
  verified = false and "needs review: ..." and the value kept, for the owner to confirm the column. Reason:
  code cannot prove which column a number came from. On the first run this held every figure (all 20),
  because annual report tables print two years side by side.
- **Not found rows (owner, 15 Nov 2026):** an item with no figure gets a row with value null. If a sentence
  in the report says the item does not exist (searched by code, e.g. "does not have any employee stock
  option scheme"), that sentence and page are the quote. Otherwise the note lists the pages that mention
  the item. Asked but not answered: "not found on the pages sent".
- **TITAN R&D is NOT stored as "not tracked (per BRSR)",** although first asked that way. Checking the
  page showed the BRSR line ("R&D - - Not being tracked as per BRSR requirement", PDF page 231) answers a
  different question (share of R&D spent on sustainability technologies). TITAN does print R&D for the
  company alone: Rs 32.28 crore FY26 in the Board's report (PDF page 141) and Rs 12.99 crore in the
  integrated report KPIs (PDF page 65); these two differ and neither is consolidated. Stored as "not found:
  no consolidated figure", with the pages that mention R&D. My earlier "not tracked" summary was wrong.
- **`page` is the PDF page number from 1,** not the printed page number (TCS PDF page 167 is printed 196).
- **published_at = end of the filing day, India time.** Filing dates given by the owner: TCS FY26
  2026-05-15, TITAN FY26 2026-07-03. The NSE annual reports page could not be read by an automated request
  (timed out); it was not worked around, so no later date was checked (not verified).
- **The raw payload keeps the page texts sent and the LLM's answers,** so the checks re-run offline with
  reparse and no new LLM call.
- **Values arriving as text** ("1,820", "(77)") are read without commas and brackets. Found on the first
  real run: TCS values came back as strings and were wrongly rejected; fixed, and the stored answer was
  re-checked from the cache (0 tokens).
- **Known limits:** the check cannot tell which column a number came from (hence needs review); pypdf can
  break table text (TCS non-current lease line reads "Lease liabilities 9 453 7,838" and the LLM took 453,
  which looks wrong); a thinking model's thinking counts inside max_tokens (4,000).
- **First real run, 15 Nov 2026 (claude-sonnet-5-5):** 2 API calls, 7,703 input + 2,139 output = 9,842
  tokens (dry-run ceiling 14,206), plus 1 cache hit.

## Report facts: owner review (15 Nov 2026)

- **Confirm appends, never edits:** `python -m smt.report_facts confirm SYMBOL:item:YYYY-MM-DD --note "why"`
  copies the current row through an adapter (owner_review) with verified = true and check_note "confirmed by
  owner review: <note>". The confirmation payload is kept as raw.
- **A confirmation's published_at = the time of confirmation,** not the filing day. Reason: the lake keeps
  the newest ingestion per key, so a same-key confirmation would be undone by any later reparse of the LLM
  extraction. A new key avoids that, and a backtest before the review correctly sees the unconfirmed row.
  Readers take the latest row per instrument, fiscal year and item by published_at
  (owner_review.current_facts).
- **Confirmed 15 Nov 2026:** TITAN's 5 items for FY26 and FY25 (10 rows: right-of-use depreciation, lease
  interest, current and non-current lease liabilities, ESOP expense; 17.3 and 14.2 are note numbers; the
  ESOP jump from 13 to 78 is noted, Titan has a 2023 performance stock unit scheme) and TCS lease interest
  778 / 631 (2 rows). The first TITAN confirmations doubled the note prefix; corrected by appending a second
  confirmation with the owner's exact words (both kept, the newer one wins).
- **Owner-entered figures (`add`):** for items the LLM is not asked for. Code still checks the quote is on
  that PDF page word for word, the unit is stated there and the value is in the quote; the several-numbers
  rule does not apply (the owner has chosen the column). published_at = end of the filing day. First one:
  TITAN rd_expense_standalone FY26 Rs 32.28 crore, PDF page 141, quote "c) Total 32.28 26.35", note "does not
  match Rs 12.99 crore on page 65; standalone only". New enum value rd_expense_standalone (contract rule 3).
- **Lease interest rate check, future runs:** interest on lease liabilities (latest year) / average of total
  lease liabilities (current + non-current, both years) must be 3% to 15%; otherwise every lease row
  (interest, current, non-current) is flagged "needs review: implied lease interest rate X% is outside 3% to
  15%". Not worked out when a figure is missing. Runs on reparse too, but never touches confirmed rows.
  Limit: it would not have caught TCS's 453 (778 / average of 2,283 and 9,392 = 13.3%, inside the range).
- **TCS non-current lease FY26 is very likely 9,453, not 453 (not yet confirmed):** the lease note (PDF page
  201) gives total lease liabilities at year end 11,283 (FY26) and 9,392 (FY25); less current 1,830 and 1,554
  gives 9,453 and 7,838. The balance sheet line extracts as "Lease liabilities  9 453 7,838".

## Report facts: corrections and the lease sum check (20 Nov 2026)

- **Correct appends, never edits:** `python -m smt.report_facts correct SYMBOL:item:YYYY-MM-DD --value N
  --note "why"`. N is in the row's stated unit (9453 for Rs 9,453 crore). The new row keeps the original quote
  and page, has verified = true and check_note "corrected by owner review: <note>", source owner_correction,
  published_at = time of correction. The wrong row stays stored and is never used (older, not verified).
  The quote no longer contains the value on a corrected row; the note says why.
- **TCS decisions (owner, 20 Nov 2026):** confirmed R&D 421 / 420, right-of-use depreciation 1,866 / 1,820,
  current lease liabilities 1,830 / 1,554, non-current FY25 7,838. Corrected non-current FY26 453 -> 9,453
  crore ("PDF text split 9,453 as '9 453'; note total 11,283 minus current 1,830 = 9,453").
- **New owner item rd_and_innovation_total** (TCS consolidated 2,900 FY26, 2,630 FY25; page 47, quote checked
  by code). Kept apart from rnd_expenditure: TCS's innovation centre spend (2,479) is about six times its R&D.
- **New LLM item lease_liabilities_total** (year-end total from the lease note), asked for in future runs so
  the sum check has a total. Adds one item to the lease call; no extra call when it shares the lease pages.
- **Lease sum check, future runs:** current + non-current lease liabilities must equal the note total within
  1 crore, for each year the note gives a total; otherwise both rows get "needs review: lease sum mismatch
  (...)". No total, no check. On the TCS case it flags FY26 (1,830 + 453 vs 11,283) and passes FY25.
  It catches what the interest rate check missed (TCS's 453 gave 13.3%, inside 3% to 15%).
- **Existing TCS and TITAN rows were not re-extracted** (no new LLM call); the new item and check apply to
  future runs and to reparse.

## Valuation stage B step 2: leases and R&D in the numbers (20 Nov 2026)

- **Maths in src/smt/analysts/adjustments.py (pure functions); valuation.py calls them.** Facts only through
  owner_review.current_facts at the as_of market close (15:30 IST, same as load_financials), only rows with
  verified = true and a value. Missing is never zero: no figure, and the reason in could_not_check.
  Existing features are unchanged; new ones: val_lease_liabilities, val_ev_incl_leases,
  val_ev_ebitda_incl_leases, val_ev_sales_incl_leases, val_roic_incl_leases, val_rd_asset_rough,
  val_roic_rd_rough, val_roic_rd_wide_rough. Lenders: no change.
- **Lease figure = current + non-current lease liabilities,** both verified, for the latest fiscal year end on
  or before the balance sheet date. If that year has a lease row but not both verified, there is no figure
  (no silent fall back to an older year). **400-day limit:** a year end more than 400 days before the date is
  not used (a September balance sheet uses the March before it, 183 days).
- **EV including leases** = EV + lease figure on the EV date; EV/EBITDA and EV/Sales use the same EBITDA and
  revenue. **Invested capital including leases** = invested capital + lease figure at each date used (closing
  and opening); either missing means no figure.
- **EBIT and EBITDA are not adjusted for leases.** EBIT = profit before tax and exceptional items + finance
  costs - other income; under Ind AS 116 lease interest is inside finance costs, so it is already added back,
  and right-of-use depreciation stays in depreciation. Checked per stock: interest on lease liabilities for
  the lease figure's fiscal year must be <= that year's finance costs; if larger, or not verified, no
  lease-adjusted figures. FY26: TCS 778 vs 1,227; TITAN 247 vs 1,180 (Rs crore).
- **Fiscal-year finance costs = the 4 quarters added up; if a quarter is not loaded, the full-year figure filed
  with the March results** (the same 12 months, from the same filings). Reason: neither TCS's nor TITAN's
  June 2025 quarter is loaded (results files start with Sep 2025), so the 4 quarters could not be added.
  The owner asked for "the sum of the 4 quarters"; this fallback is my addition, stated here for review.
- **R&D, Damodaran method, rough:** consolidated basis only, never rd_expense_standalone. 3-year life, straight
  line. Research asset = R0 + 2/3 R1 + 1/3 R2; amortisation = (R1 + R2 + R3) / 3; adjusted NOPAT =
  EBIT x (1 - tax rate) + (R0 - amortisation), no tax on the adjustment; adjusted invested capital = invested
  capital including leases + research asset. R0's year end must be within 400 days of the ROIC date.
- **Flat-history assumption:** at least 2 consecutive verified years (R0, R1) are needed; years before the
  earliest known year are assumed equal to it. With 2 years: asset = R0 + R1, amortisation = R1, EBIT change =
  R0 - R1. Every R&D sentence says "rough" and names the assumption and that latest fiscal year R&D is set
  against 12-month EBIT.
- **Owner's choice: narrow leads (rnd_expenditure), wide next to it (rd_and_innovation_total),** and the wide
  one says innovation spend is not R&D by the accounting definition.
- **First real run, 20 Nov 2026 (no LLM):** TCS ROIC 87.5% before leases, 74.6% including leases (lease
  liabilities 11,283 and 9,392), rough 73.7% narrow and 69.5% wide; EV/EBITDA 10.9x -> 11.1x. TITAN ROIC
  24.7% -> 22.5% (leases 3,173 and 2,681); EV/EBITDA 43.0x -> 43.3x; no R&D numbers (consolidated R&D not
  found).

## Analyst checklist before every plan or build (owner, 20 Nov 2026)

- **docs/ANALYST_CHECKLIST.md is run before any plan or build:** Part A (12 gates) always, the Part B modules
  and Part C sector playbook that fit, Part D for anything coded. The "Checklist result" block goes at the
  top of the reply, each line pass, gap or not verified; a gap is fixed in the task or logged here as an
  open item. Its stop rules apply (stop and ask the owner when no decision is served, a needed input cannot
  be verified, the method would be generic where a sector playbook says otherwise, or two or more gaps
  cannot be fixed in the task). Reason: the standard is what a great analyst or fund manager would build,
  not a generic solution. Added to CLAUDE.md (read-first line and working rules). The checklist text is
  the owner's and is not edited by Claude.

## Gold on loan as debt, and the R&D materiality line (20 Nov 2026)

- **Gold on loan (jewellers) is treated as debt:** it funds inventory and its interest is in finance costs.
  EV including leases and gold on loan = EV including leases + gold on loan on the EV date; invested capital
  adds it at the closing and opening dates (both needed); EBIT is not changed. This figure leads, then
  "including leases" (the view of analysts who treat it as working capital), then "before leases". The text
  says so. New owner item gold_on_loan (report_facts add, quote checked on the PDF page); its
  fiscal_year_end column holds the balance sheet date, which may be a half-year end.
- **Exact date rule:** used for a balance sheet date only if a verified row exists for that same date. No
  carry forward and no 400-day rule, because it moves with the gold price and the stock held. A verified row
  for another date gives one fact line ("Gold on loan Rs X crore as of <date>; not in EV or ROIC: no figure
  for <date>") and a reason. No lease figures: not added. No gold rows: output unchanged.
- **TITAN checks (code text search of TITAN_FY26.pdf, no LLM):**
  (a) consolidated balance sheet, PDF page 374: "(ii) Gold on loan 17.2 16,070 7,810" (FY26, FY25), matching
  the public figure the owner gave (16,070 / 7,810).
  (b) double count 1: borrowings on that page 93 (non-current) + 11,285 (current) = 11,378; the lake for
  2026-03-31 (consolidated XBRL) has the same 93 + 11,285. Gold on loan is not inside borrowings in March
  2026: pass. In the March 2026 XBRL, gold on loan appears to sit inside TradePayablesCurrent (18,934),
  which is not in EV or invested capital (not verified).
  (c) double count 2: finance costs note, PDF page 430: "Gold on loan {refer note 17.2} 320 174" listed under
  interest expense, total finance costs 1,180 / 953. Its cost is in finance costs, which EBIT adds back: pass.
  (d) no TITAN results PDFs are stored; no September gold figures can be entered yet.
- **Open item, September 2026 (not verified):** the lake's TITAN borrowings_current jumps from 11,285 (March
  2026) to 24,191 (September 2026) while trade payables fall from 18,934 to 3,932 and other current financial
  liabilities rise from 2,530 to 13,536. Gold on loan may have moved inside borrowings or other liabilities
  in the September XBRL. Check (b) must be repeated for 2026-09-30 before a September gold row is added;
  stage A EV and invested capital for September 2026 may already include it.
- **R&D materiality, 1% rule:** share = latest fiscal year R&D / 12-month revenue to the ROIC date. Under 1%:
  one short line "R&D is X.X% of revenue: not material for this company (rough ROIC with it capitalised:
  Y%)"; features still stored. 1% or more: the full rough sentence plus the share. R&D uses the lead invested
  capital (with gold on loan when present). TCS: narrow 0.1% (not material), wide 1.0% (about 1.01%, kept).

## TITAN gold on loan for September 2025 and 2026 (21 Nov 2026)

- **Source:** TITAN results PDFs (board meeting outcome), saved by the owner from a browser: half year to
  30 Sep 2025 (board meeting Monday 3 Nov 2025) and half year to 30 Sep 2026 (board meeting Thursday 5 Nov
  2026), stored as data/annual_reports/TITAN_2025-09-30_results.pdf and TITAN_2026-09-30_results.pdf (data/
  is not in git). A curl download from titancompany.in got a 406 bot-filter page; not worked around.
- **Checks (code text search, no LLM):**
  30 Sep 2026, consolidated balance sheet page 11: "(ii) Gold on loan 12,278 16,070". Borrowings 74 + 24,191,
  same as the lake (difference 0). 30 Sep 2025, page 16: labels and values are in separate text columns; the
  value column in order reads borrowings 9,099, gold on loan 16,088, lease 377; both liability sub-totals add
  up (3,337 and 35,890). Borrowings 547 + 9,099, same as the lake. Gold on loan is not inside borrowings on
  either date: the September 2026 open item from 20 Nov is closed (the jump in borrowings is real bank debt).
  The March columns in both PDFs (16,070 and 7,810) match the annual report.
- **XBRL tag holding gold on loan (not verified):** 30 Sep 2025 in trade payables to other creditors
  (18,342 = 16,088 + 2,254); 30 Sep 2026 in OtherCurrentFinancialLiabilities (13,536 = 12,278 + current lease
  486 + other 772). The tag changes between filings. EV and invested capital use only equity, borrowings and
  cash, so neither tag changes our numbers.
- **Odd numbers kept as printed (not verified):** the Sep 2026 text shows "Other financial liabilities m";
  772 is worked out from the sub-total 48,962. Sep 2025 XBRL current financial liabilities 29,524 = PDF
  29,901 less the current lease 377 (filed under other current liabilities 6,034 = 5,657 + 377).
- **Rows added by the owner** (report_facts add, verified, published_at = filing day): 2025-03-31 7,810;
  2025-09-30 16,088; 2026-03-31 16,070; 2026-09-30 12,278.
- **TITAN as of 20 Nov 2026** (12 months to 2026-09-30; ROIC averages 2025-09-30 and 2026-09-30; leases from
  FY26 and FY25 annual reports, 3,173 and 2,681; gold 12,278 and 16,088):
  EV 452,676 / 455,849 / 468,127 crore; EV/EBITDA 43.0x / 43.3x / 44.5x; ROIC 24.7% / 22.5% / 15.6%
  (before leases / including leases / including leases and gold on loan). Gold on loan fell from 16,070 to
  12,278 in six months while current borrowings rose from 11,285 to 24,191: TITAN seems to have moved funding
  from gold loans to bank debt (not verified), so the "before leases" EV already carries more debt.

## Valuation stage C step 1: cost of capital, and ROIC against it (23 Nov 2026)

- **Inputs, owner-entered:** gsec_10y, mature_erp, india_crp, india_default_spread in
  src/smt/assumptions/cost_of_capital.json, each with value (percent), source, source_date, entered_on.
  Each `python -m smt.assumptions set` writes a new version; the old file goes to history/ (never
  overwritten), like desks/weights. Never guessed, never fetched from the web. Shipped empty (kept as
  history/cost_of_capital_empty.json). Values outside 0.3 to 25 are refused, so 0.0645 typed for 6.45% is caught.
- **As-of rule:** a run on date D uses the latest version entered on or before D (by entered_on date, then
  version number). No look-ahead: inputs entered on 23 Nov are not used for a run dated 20 Nov.
- **Stale limits (by source_date):** G-sec older than 45 days (bond yields move weekly); the three
  Damodaran inputs older than 400 days (published once a year, updated mid-year). Stale is flagged in the
  text and the gaps, not refused. A missing input means no cost of capital, with the reason.
- **Formula:** risk-free = G-sec - India default spread. Cost of equity = risk-free + beta x (mature ERP +
  India CRP). Cost of debt (before tax) = 12-month finance costs / average debt. WACC = E/(E+D) x cost of
  equity + D/(E+D) x cost of debt x (1 - tax rate), market value weights. Spread = lead ROIC - WACC.
- **Why the default spread is taken off:** the Indian government bond yield already holds India's default
  risk, and the country risk premium holds it too (Damodaran's CRP is built from the default spread). Using
  the full G-sec yield with the CRP would count it twice. Tested: a test fails if it is not taken off.
- **Beta rule and its limits:** raw = 1-year beta to Nifty 50 from daily closes (the same function the
  market_beta analyst stores; reverse_valuation calls it directly because the desks run in parallel and
  today's stored value may not exist yet). Base = 0.67 x raw + 0.33 (Blume: betas drift towards 1), kept
  inside 0.6 to 1.5; low and high = base -/+ 0.2. Labelled rough: one year of prices is noisy (R2 on
  23 Nov: TCS 0.13, TITAN 0.37, M&MFIN 0.26); an industry beta from peers comes later.
- **Cost of debt rule:** debt = borrowings, plus lease liabilities, plus gold on loan, following the view
  that leads ROIC in capital_returns (same dates, same rules; valuation.lead_capital repeats its steps and a
  test checks both give the same ROIC and debt). Average over the ROIC dates. Used only inside 4% to 15%;
  outside, or debt zero or missing: cost of equity only, no WACC and no spread, with the reason. When the
  lead view is before leases, the text says finance costs include lease interest that the debt does not.
- **Weights:** E = market value (share count x latest close, as relative_valuation); D = debt on the latest
  date. Tax rate = the one ROIC uses.
- **Lenders:** no WACC (debt is raw material). ROE (as capital_returns) against cost of equity low/base/high.
- **Analyst reverse_valuation** (equity research, step 1): views neutral, facts only. Added to the desk and so
  to the call logger: it adds new rows from now on; stored history is not changed; desk weight is 0.
- **First run, as of 23 Nov 2026** (inputs: G-sec 7.2116% CCIL 6.94% GS 2036, 20 Nov 2026; mature ERP 4.23%,
  India CRP 2.85%, default spread 1.87%, Damodaran, 5 Jan 2026; risk-free 5.34%; prices to 13 Nov, flagged
  more than 7 days old):
  TCS: beta raw 0.75, base 0.83; cost of equity 9.8-12.7% (base 11.2%); cost of debt 12.8% (finance costs
  1,328 / average debt 10,338, leases only; not verified why finance costs exceed lease interest); weights
  99/1; WACC 9.8-12.6%; ROIC 74.6%; spread +62.0 to +64.8 points.
  TITAN: beta raw 1.02, base 1.01; cost of equity 11.1-13.9% (base 12.5%); cost of debt 4.5% (1,521 / 34,066,
  with leases and gold on loan; near the 4% floor, gold loans are cheap, not verified); weights 92/8; WACC
  10.4-13.0%; ROIC 15.6%; spread +2.6 to +5.2 points.
  M&MFIN (lender): beta raw 1.45, base 1.30; cost of equity 13.1-16.0% (base 14.5%); ROE 13.8%; spread -2.2
  to +0.6 points.
