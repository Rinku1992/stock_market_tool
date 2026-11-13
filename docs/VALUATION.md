# Valuation

How the equity research desk judges whether a price is fair. Code computes every number. The LLM
never does maths. Rules here are fixed before they are run on outcomes (see DECISIONS.md).

## The five questions

| # | Question | Analyst | Metrics |
|---|---|---|---|
| 1 | Are the numbers clean? | accounting adjustments (stage B) | exceptional items, other income share, related-party and auditor notes, restatements |
| 2 | Is the profit backed by cash? | earnings power (stage A) | 12-month reported and core profit, operating cash, capex, free cash flow, owner earnings, cash conversion, accruals |
| 3 | Does the business earn more than its capital costs? | capital returns (stage A) | ROE, ROIC (EBIT x (1 - tax rate) / invested capital); lenders: ROE, return on assets, credit cost |
| 4 | What does the price assume? | relative valuation (stage A), reverse valuation (stage C) | market value, P/E, core P/E, earnings yield, P/B, dividend yield, EV, EV/EBITDA, EV/Sales, FCF yield; implied growth |
| 5 | What is it worth, with a margin of safety? | value range (stage D) | low / base / high value from the above, margin of safety against the price |

## Rules for every analyst

- Plain code, no LLM. Views stay neutral until a backtest supports a rule.
- Point in time: only filings published by the 15:30 IST close of the as-of date, and prices up to that date.
- Missing is never zero. Shares, face value, equity, revenue and total assets of zero count as missing.
  A filed 0 is real for dividends and minority interest. A missing quarter is named, not filled.
- 12 months (TTM) = the last 4 quarters. Cash flow (filed half-yearly) = half year to this September
  + full year to March - half year to last September. Averages use the balance sheet one year before.
- Consolidated figures first; standalone only when no consolidated filing exists. The basis is stated.
- Share count = paid-up capital / face value, checked against share capital on the balance sheet (2%
  tolerance). A jump above 20% is taken as a split or bonus: only counts from then on are used.
- Cash = cash, bank balances and current investments. Lease liabilities are not in quarterly filings
  and are left out of debt, EV and invested capital (stated in the report).
- EBIT = profit before tax and exceptional items + finance costs - other income.
- A loss means no P/E. Negative EBITDA means EV/EBITDA is "not meaningful".

## Sector rules

| Type | Router rule | What changes |
|---|---|---|
| Lender | Loans, or interest income with credit cost, in its filings | ROE, return on assets and credit cost replace ROIC. P/B leads. No EV, EV/EBITDA, EV/Sales, FCF yield, owner earnings, cash conversion or accruals (lending is operating cash outflow; debt is raw material) |
| Real estate | Index industry "Realty", or a filing segment named "Real Estate" / "Realty" | P/B leads. P/E carries a warning (profit is booked on handover, so it is lumpy). EBIT and EBITDA lead WITH other income, also shown without; ROIC uses the version with other income. Other income may include interest from joint ventures and project partners (not verified) |
| General | Everything else with filings | All metrics |
| Unknown | No filings | No report numbers |

Banks, insurers and holding companies get their own rules later.

## Build stages

| Stage | Builds | Status |
|---|---|---|
| A | earnings power, capital returns, relative valuation; lender and real estate rules; split, basis and loss traps | Built 13 Nov 2026 |
| B | accounting adjustments: clean-up of exceptional and one-off items, lease liabilities and R&D from annual reports | Planned |
| C | reverse valuation: growth and returns the price implies; multiples against own history and peers | Planned |
| D | value range and margin of safety; first directional views, only after a backtest | Planned |
