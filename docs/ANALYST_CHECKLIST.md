# Analyst checklist

**Rule:** run this checklist before every roadmap, plan, solution or build. No exceptions.

**Standard:** not a generic tool. Build what a great analyst or fund manager would build.
Ask: "Would a top buy-side analyst trust this number with real money?"

## How to use it

1. Run **Part A** (12 gates) for every task.
2. Pick the modules in **Part B** that fit the task. Pick the sector playbook in **Part C** for each stock type touched.
3. Run **Part D** (engineering and data) for anything that will be coded.
4. Show a **Checklist result** block in the reply (format in Part E) before the plan or the code.
5. Each line is `pass`, `gap` or `not verified`. A gap is either fixed in the task, or logged as an open item in `DECISIONS.md`. Never hide a gap.
6. Do not tick a box from memory. If a fact was not checked, write `not verified`.

---

## Part A: the 12 gates (every task)

### 1. Decision
- Which investor decision does this serve: buy, hold, add, trim, exit, or avoid?
- Which horizon: short (3-5 months), mid (6-12 months), long (2 years)?
- What will the owner do differently because of this output? If nothing, do not build it.

### 2. Best practice
- How do the best people do this? Name the method and the source (Koller, Damodaran, Penman, Mauboussin, Graham, CFA curriculum, rating agency method, sell-side practice).
- Is there a known standard formula? Use it. No home-made method when a known one exists.
- Where the experts disagree, say which version is used and why.

### 3. Sector fit
- Does the method change by stock type (bank, NBFC, insurer, IT services, consumer, pharma, auto, real estate, commodity, utility, holding company)?
- What would a sector specialist add, remove or rename?
- Which metric leads for this sector, and which metric misleads?
- Is there a sector-specific debt-like or asset-like item (Part C)?

### 4. Materiality
- Does this item move the answer enough to matter for this company? State its size against revenue, profit, equity or enterprise value.
- If it is small, say "not material". Do not show false precision.
- Spend effort where the money is: the 3 or 4 drivers that explain most of the value.

### 5. Accounting reality
- Which Ind AS rule applies (leases 116, revenue 115, financial instruments 109, consolidation 110, business combinations 103, share-based payment 102)?
- Consolidated or standalone? Never mix the two in one ratio.
- Same period on both sides of every ratio (12 months vs fiscal year vs quarter).
- Stock figures (balance sheet) use the right date; flow figures (P&L, cash flow) the right window. Average vs closing balance stated.
- One-offs, exceptional items, prior-period items, restatements, changes in accounting policy.
- Acquisitions, demergers, splits, bonuses, buybacks: are the periods comparable?
- Minority interest, associates and joint ventures handled the same way in numerator and denominator.
- Units and signs (crore, lakh, million; brackets mean negative).

### 6. What can fool us
- Double counting (for example adding back a cost that is already added back).
- Look-ahead: using a figure before it was published.
- Stale data: a figure too old for the date it is used on.
- Survivorship: only looking at companies that still exist.
- Numbers management can shape: adjusted profit, other income, capitalised costs, provisions, revenue timing.
- A ratio that looks good for a bad reason (high ROE from high debt or a buyback, low P/E at a cycle peak, high yield before a dividend cut).
- Small or negative denominators (tiny equity, negative invested capital, a loss).
- Base effects and seasonality in growth rates.

### 7. Data trust
- Every input has a source, a date published, and an as-of date.
- Only verified rows are used. Unverified rows are never used, even if they look right.
- Missing is never zero. A filed zero is accepted only where zero is plausible.
- LLM-extracted numbers pass code checks (exact quote, unit, sum checks) before use.
- Is the source allowed (licensed API, official filing, terms permit automation)?
- Is there a second source to cross-check against?

### 8. Compared with what
- A number alone is not an answer. What is it read against?
  - cost of capital (for returns),
  - the company's own history (5 to 10 years, across a cycle),
  - sector peers on the same definitions,
  - the market,
  - what the current price already assumes.
- Is the comparison like for like (same accounting, same period, same adjustments)?

### 9. Range, not a point
- Is there a bull, base and bear view, or at least a sensitivity?
- Which 2 or 3 assumptions drive the result most?
- What is the downside if we are wrong, not only the upside if we are right?

### 10. Checkable
- Can a test check it by hand with round numbers?
- Can it be cross-checked against a public figure for at least one real stock?
- If our figure differs from public sites, do we know why?

### 11. Honest label
- What is rough, assumed or not verified? Say it in the output itself, not only in the notes.
- What fact would change the conclusion?
- Facts and views are kept apart. No verdict without evidence.

### 12. Scope
- Is the work already done in the repo? Check first.
- One task only. Existing features keep their values and meaning.
- What is left out on purpose, and where is it logged?

---

## Part B: modules (pick what fits the task)

### B1. Financial health and balance sheet strength
**Liquidity**
- Cash and liquid investments against debt due within 12 months.
- Is the cash real and free? Restricted cash, cash held in subsidiaries or abroad, cash lent to group companies, margin money.
- Unused bank lines, if disclosed.

**Leverage**
- Net debt / EBITDA, net debt / equity, interest cover (EBIT / interest), and fixed-charge cover including leases.
- Use lease-consistent figures: if EBITDA is before lease costs, debt includes lease liabilities.
- Debt maturity profile, fixed vs floating rate, foreign currency debt and hedging.
- Covenants and rating actions (upgrade, downgrade, outlook change, rating withdrawn).

**Debt-like items a specialist adds**
- Lease liabilities.
- Supplier credit and acceptances, bills discounted, factoring of receivables.
- Gold or metal on loan (jewellers), customer advances that work like funding.
- Pension and gratuity deficits.
- Preference shares, compulsorily convertible instruments, put options to minority holders.
- Contingent liabilities, tax disputes, guarantees and letters of comfort for group companies.
- Deferred payment for acquisitions or spectrum.

**Working capital**
- Receivable days, inventory days, payable days, and the cash conversion cycle. Trend over years, not one point.
- Are receivables or inventory growing faster than sales?
- Unbilled revenue and contract assets (IT, infrastructure).
- Is profit growth funded by stretching suppliers?

**Cash generation**
- Operating cash against profit over 3 to 5 years (cash conversion).
- Free cash flow after all capex and lease payments.
- Are dividends and buybacks paid from free cash flow or from new debt?
- Capex: maintenance vs growth, and how it is funded.

**Stress**
- What happens to cover ratios if EBITDA falls 20% to 30% or rates rise 2 points?
- Refinancing risk in the next 12 to 24 months.
- Distress scores (Altman Z, Piotroski F) only as a cross-check, never as the answer.
- Lenders: this module does not apply. Use the bank playbook.

### B2. Earnings quality
- Reported profit vs core profit (before exceptional items) vs owner earnings.
- Accruals: profit running ahead of cash.
- Other income as a share of profit before tax. Is it recurring?
- Capitalised costs (interest, development costs, software) that lower today's expense.
- Changes in depreciation life, provisions, write-backs, inventory valuation.
- Revenue recognition: bill-and-hold, channel stuffing, percentage of completion, unbilled revenue.
- Tax rate against the statutory rate. Why is it different? Is it lasting?
- Related-party sales and purchases.
- Segment profit: does one segment hide losses in another?
- Quarter patterns: a jump only in the fourth quarter.

### B3. Capital returns and business quality
- ROIC against cost of capital. Growth creates value only when ROIC is above it.
- Invested capital defined once and used the same way: equity + debt + leases - excess cash; goodwill kept in and also shown without.
- Incremental ROIC (return on new capital), not only the average.
- Stability of returns over a cycle.
- DuPont split of ROE: margin, asset turnover, leverage. Is ROE high for a good reason?
- Intangibles: R&D, brand and customer acquisition spend that accounting expenses. Capitalise only where material.
- Capital allocation record: acquisitions, buybacks, dividends, dilution.
- Sources of moat: pricing power, switching costs, scale, network, licences. Evidence, not adjectives.

### B4. Growth
- Organic vs acquired growth. Volume vs price vs mix. Constant currency where relevant.
- Unit drivers (stores, customers, volumes, realisation), not only total revenue.
- Market size and share: is the growth runway real?
- Operating leverage: how do margins move with growth?
- Reinvestment needed for each rupee of growth.
- Management guidance against delivery in past years.

### B5. Valuation
- Clean the numbers first (B2), then value.
- Multiples on consistent numerator and denominator: equity value with equity earnings; enterprise value with pre-interest figures.
- Enterprise value complete: market value + debt + leases + other debt-like items + minority interest - cash and non-operating investments; associates treated on purpose.
- Share count: diluted, after ESOPs, convertibles, splits and bonuses.
- Trailing vs forward, reported vs core. State which.
- Cyclicals: normalised earnings over a cycle, never peak or trough earnings.
- Reverse valuation: what growth and margins does the price assume? Is that believable against history?
- Cost of capital: government bond yield, equity risk premium and beta are written assumptions with sources.
- Terminal value share of total value; terminal growth below long-run nominal growth.
- Value range with a margin of safety. Never a single point.
- Against own history and peers on the same definitions.
- Holding companies and conglomerates: sum of the parts and holding company discount.

### B6. Governance and forensic checks
- Promoter holding, pledge and changes. Promoter group debt.
- Related-party transactions: size, trend, terms. Loans and advances to group companies.
- Auditor: who, changes, resignations, qualified opinions, emphasis of matter, CARO remarks.
- Board and key people: independent director exits, CFO or company secretary changes.
- Contingent liabilities and litigation against net worth.
- Cash on the books that earns no interest, or interest income that does not match cash balances.
- Frequent equity raises while reporting profits. Capital work in progress that never gets capitalised.
- Royalty and brand fees to the parent.
- Regulatory actions (SEBI, RBI, stock exchange queries), tax raids, credit rating notes.
- Governance can override value. A red flag is reported even when the numbers look cheap.

### B7. Ownership and flows
- Promoter, FII, DII, mutual fund and retail holding trends.
- Bulk and block deals, insider trades, pledges created or released.
- Free float and index inclusion or exclusion.
- A flow is a fact, not a signal, until calibration shows an edge.

### B8. Market activity and technical signals
- Any signal must be tested against the median stock, with rules fixed before the run.
- No proven edge means weight 0. Context can still be shown as fact.
- Liquidity: can the owner's position be bought or sold without moving the price?
- Corporate actions adjusted in the price series.
- Check the result across periods, not one lucky window.

### B9. Portfolio and holdings decisions
- Position size against conviction and downside.
- Concentration by stock, sector, group and factor. Correlation between holdings.
- Tax and cost of acting (capital gains, brokerage, impact cost).
- What would make us exit? Write the exit rule before the buy.
- Has the original reason for holding changed?
- Compare with simply holding the index.

### B10. News and events
- Primary source first (exchange filing, company release, regulator order).
- Date and time of the event against the market close. No look-ahead.
- Is it new, or already in the price?
- Is it a one-off or does it change earnings power, risk or governance?
- Dedupe copies of the same story. Rumours are labelled as rumours.

### B11. Backtest and calibration
- No look-ahead, no survivorship, corporate actions handled.
- Rules, universe and success measure fixed before the run.
- Costs and liquidity included.
- Out-of-sample period kept apart. Stability across sub-periods.
- Number of things tried is recorded (many tries create false winners).
- Benchmark is the median stock or the index, not zero.
- A weight changes only through the calibration officer, with a version.

---

## Part C: sector playbooks

### Banks
- Lead with P/B against ROE, ROA, and asset quality. No EV, EBITDA, ROIC or free cash flow.
- Net interest margin, cost of funds, CASA share, loan and deposit growth, credit-deposit ratio.
- Gross and net NPA, slippages, provision cover, credit cost, restructured and special-mention loans, write-offs.
- Capital: CET1 and total capital ratio. Dilution risk.
- Cost to income. Fee income share. Treasury gains are not core.
- Loan mix: unsecured retail, microfinance, corporate, concentration.
- Liquidity coverage. Asset-liability gaps.

### NBFCs and housing finance
- As banks, plus: funding mix and cost, reliance on short-term paper, asset-liability match.
- Stage 2 and stage 3 assets, expected credit loss cover, collection efficiency.
- Leverage (assets / equity), rating, securitised and off-book loans.
- Growth against asset quality in new loan books (seasoning).

### Insurers
- Life: value of new business and its margin, embedded value and return on it, persistency, product mix, solvency ratio.
- General: combined ratio, loss ratio, expense ratio, investment yield, solvency ratio.
- P/E and P/B mislead for life insurers. Use price to embedded value.

### IT services
- Constant-currency growth, deal wins (total contract value), order book.
- Utilisation, attrition, headcount, subcontracting cost, wage hikes, onsite vs offshore mix.
- Client and vertical concentration, top-client growth.
- Margins against currency moves. Hedging gains are not core.
- Cash rich: separate cash from the business. Payout ratio and buybacks.
- Receivable days and unbilled revenue.
- R&D is usually small; say "not material" unless the company sells products or platforms.

### Consumer, retail and jewellery
- Volume vs price growth, same-store sales growth, store count and store economics.
- Gross margin against input costs. Advertising spend as a share of sales (brand investment).
- Inventory days and channel inventory.
- Lease liabilities are large for retailers: use lease-consistent EV and ROIC.
- Jewellers: gold on loan and how gold price is hedged, making-charge margin, studded share, franchise vs own stores, inventory as most of capital.
- Distribution reach and channel mix (general trade, modern trade, online).

### Pharma and healthcare
- R&D spend and how much is capitalised. Capitalise expensed R&D when material.
- US generics: price erosion, product pipeline, plant inspections and warning letters.
- India branded business: growth against the market, price control list exposure.
- Product and plant concentration. Litigation and patent settlements.
- Hospitals: occupancy, revenue per bed, payor mix, new-bed ramp-up.

### Auto and auto parts
- Volumes by segment, market share, realisation per unit, product cycle.
- Capacity utilisation, commodity cost pass-through, operating leverage.
- Cyclical: normalised margins over a cycle.
- Captive finance arm: value it apart, like a lender.
- Electric vehicle transition spend and capitalised development costs.

### Real estate
- Lead with P/B or net asset value. P/E is lumpy because revenue is booked at handover.
- Pre-sales, collections, construction spend and operating cash by project.
- Net debt, customer advances, land bank and unsold inventory (months to sell).
- Other income and joint venture structures. Project partner loans.
- Rental assets valued apart, on yield.

### Commodities, metals, cement, oil and gas, chemicals
- Normalised margins over a full cycle. Never peak earnings on a low multiple.
- Position on the cost curve. Realisation and spread against benchmarks.
- Net debt / EBITDA at trough earnings.
- Capacity additions in the industry (supply) against demand.
- Inventory gains and losses are not core. Government pricing and duties.

### Utilities, infrastructure and capital goods
- Regulated return on equity, tariff orders, receivables from state distribution companies.
- Order book against revenue, order inflow, execution rate, working capital in projects.
- Project debt, special purpose vehicles, guarantees. Capital work in progress.
- Concession life and traffic or volume risk.

### Telecom and platforms
- Revenue per user, subscriber adds, churn, spectrum and network capex.
- Spectrum and adjusted gross revenue dues are debt.
- Platforms: unit economics, customer acquisition cost against lifetime value, take rate, cash burn and runway.

### Holding companies and conglomerates
- Sum of the parts with listed stakes at market value.
- Holding company discount against its own history.
- Debt at the parent, guarantees to group companies, cash flow from dividends.

---

## Part D: engineering and data (anything coded)

- Code computes every number. The LLM only extracts facts with exact quotes. No LLM maths, no LLM verdicts.
- All data enters through an adapter and the data contract. Nothing is edited in place; corrections append.
- As-of discipline: every read is "as known on this date". Test that a later fact is not used.
- Missing is never zero. Test the missing case, the not-verified case and the stale case.
- Every formula is written in the output text, so the reader can re-do it.
- Every new figure has: a hand-checkable test, a real-stock cross-check, and a label if rough.
- Existing features keep their name, meaning and value. New meaning gets a new feature name.
- Guard rails for bad denominators, out-of-range rates and unit mismatches.
- Design choices and open items go into `DECISIONS.md` with the reason.
- LLM calls go through the budget guard, with a dry run first.
- No secrets read, printed or committed. Kite stays read-only.
- Tests pass locally and on GitHub before the task is called done.

---

## Part E: the Checklist result block

Put this at the top of every plan, prompt or build reply:

```
Checklist result
Task: <one line>
Modules used: <for example B1, B5, C: consumer and jewellery>

 1 Decision          pass | gap | not verified : <one line>
 2 Best practice     ...
 3 Sector fit        ...
 4 Materiality       ...
 5 Accounting        ...
 6 What can fool us  ...
 7 Data trust        ...
 8 Compared with     ...
 9 Range             ...
10 Checkable         ...
11 Honest label      ...
12 Scope             ...

Module findings: <only the gaps and the not-verified items>
Fixed in this task: <list>
Logged as open items: <list>
```

## Stop rules

Stop and ask the owner before building when:
- gate 1 fails (no decision is served);
- a needed input is not verified and there is no way to verify it;
- the method would be generic where a sector playbook says otherwise;
- two gaps or more cannot be fixed inside the task.