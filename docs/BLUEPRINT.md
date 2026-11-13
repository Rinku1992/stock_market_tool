# Blueprint

Source of truth for what we are building. Update this file when the design changes.

## Goal

Help the owner decide if a given stock is good to invest in or not, for three horizons:
short (3-5 months), mid (6-12 months), long (2 years).

Reference products: BlackRock Aladdin (portfolio and risk), Bloomberg Terminal and FactSet
(company research and data). We borrow their structure and features. We cannot match their
data depth.

### What the product must do

1. **Verdict.** For any stock: consider / avoid / no call for each horizon, with the reasons,
   the main risks, and what would change the view.
2. **Company research page.** All key facts in one place: financials, valuation, ownership,
   news, filings, peers. Each one linked to its source. (FactSet / Bloomberg style)
3. **Peer and sector comparison.** The stock against its peers and its own history.
4. **Screening.** Rank the whole universe to find candidates, not only judge one name.
5. **Portfolio view (Zerodha).** Hold / add / trim / exit for each holding, plus
   concentration, overlap and "what if" tests such as a 10% market fall. (Aladdin style)
6. **Monitoring.** Alerts when the conditions behind a view break.

### The benchmark is reliability

- Right numbers. Code computes. The LLM never does maths.
- Traceable. Every claim links to a source.
- Consistent. Same input, same verdict.
- Tested. Backtest and forward test before trusting a rule.
- Honest. Shows confidence. Says "no call" when signals disagree.

## Flow

    data office -> (grade C: refuse) -> stock type router -> desks in parallel
      -> independent review -> scoring engine -> three horizon verdicts

## Units and roles

Type: C = code, L = LLM, B = both.

**0. Data office (gatekeeper)**
symbol and corporate action resolver (C), reconciler (C), freshness clerk (C),
coverage grader (C), point-in-time librarian (C), input guard (C)

**Router**
stock type router (C), sector playbooks (C): bank/NBFC, insurer, IT services, pharma,
commodity, real estate, holding company, loss-making new-age

**1. Market news research**
event clusterer (B), news credibility (B), sentiment shift (B), news impact (C),
priced-in (C), silence analyst (B)

**2. Equity research**
business model (L), financial trend (C), forensic accounts (B), management say-do (B),
capital allocation (B), reverse valuation (C), relative valuation (C), credit (B),
legal and regulatory action (B)

**3. Industry and geopolitical research**
sector cycle (B), input cost mapper (C), policy (L), geopolitical exposure (B),
macro regime (C), peer read-through (B)

**4. Market activity and ownership research**
price structure (C), liquidity (C), ownership (C), insider and deal (C),
derivatives positioning (C), surveillance (C), crowding (B), promoter group (B),
market beta (C)

**5. Event and catalyst desk**
calendar (C), supply (B), index flow (C)

**6. Portfolio and position desk (Zerodha)**
holding context (C), overlap (C), position size (C), thesis tracker (B)

**7. Independent research review (has veto)**
evidence auditor (B), number re-checker (C), bear advocate (L), contradiction finder (L),
pre-mortem (L)

**Judgement**
base rate (C), "compared to what" (C), scenario (B)

**8. Track record office**
call logger (C), calibration officer (C)

**System**
sentinel (C), depth controller (C)

## Hard rules

1. An analyst is a plain Python function: `AnalystContext -> AnalystReport`.
2. Only `src/smt/graph.py` imports LangGraph. A test enforces this.
3. The graph is fixed. No LLM decides which analyst runs next.
4. All LLM calls go through `src/smt/llm.py`. Every call is recorded.
5. LLM extracts facts with exact quotes. Code decides what facts mean.
6. Every feature has `known_at` and `origin` (code or llm). Reads are point-in-time.
7. Scoring engine and backtest engine are plain code with no LLM.
8. Backtests before the model cutoff date use `code` features only.
9. A rule enters the scoring engine only if a backtest or forward test supports it.
10. No keys in the repo.

## Phases

| Phase | Builds | Done when | Status |
|---|---|---|---|
| 0. Foundation | Schema, feature store, LLM wrapper, graph skeleton | Dummy run gives a valid verdict | Done |
| 1a. Data contract | Vendor-free schemas, validation gate, raw + Parquet storage, adapter interface (see DATA_CONTRACT.md) | A fake vendor loads end to end | Done |
| 1b. Kite adapter | Login, daily prices, holdings | Runs on live data for 5 stocks | Prices run live for RELIANCE. Holdings not yet run live |
| 1c. Data office | Fundamentals adapter, corporate action fix, reconciler, data grade | 5 test stocks load with a grade | Needs a fundamentals vendor |
| 2a. Price analysts | Price structure, liquidity, market beta (rules not backtested, confidence capped) | Reports for RELIANCE on live data | Built, awaiting live run |
| 2c. Momentum factor | Risk-adjusted 12-1 momentum, ranked across loaded stocks, terciles | Backtest on holdings | Built, awaiting live run |
| 2d. Desk lead | DeskReport schema, weighted combine from versioned weights, LLM summary checked by code | Market activity desk returns a DeskReport | Built |
| 2e. Calibration (learning loop) | Re-score analysts on outcomes, by regime; write new weight versions | Weights change only when evidence does | Built (`python -m smt.calibrate`). Nifty 100 run: no market activity analyst passes, all weights 0. Regime reported only. Weights not written yet (owner's call) |
| 2f. Financial trend | NSE results XBRL reader (both formats), point-in-time financials, financial trend analyst | Real Reliance filings load and report | Built. Needs filings for more stocks and quarters |
| 2b. Other code desks | Router, financial trend, valuation, ownership | Each returns a report for the 5 stocks | Router built (lender / general, from filing tags; checked on M&MFIN, banks not verified). Lender playbook for financial trend built. Valuation and ownership need a fundamentals vendor |
| 3a. Backtest engine | Monthly point-in-time replay, scored on return versus Nifty 50 | Runs on live prices | Built. Only meaningful once many stocks are loaded |
| 3b. Scoring engine | Turns analyst views into a verdict, using weights that backtests support | First real verdict | Needs 3a results on many stocks |
| 4. LLM desks | News, credibility, extraction, forensic reading, input guard | Every claim carries a quote | |
| 5. Review + verdict | Review unit, three horizon committees | One full verdict page | |
| 6. Zerodha | Holdings pull, portfolio desk, sentinel | Holdings show hold/add/trim/exit | |
| 7. App | Web UI | Type a name, get the report | |

## Defaults in use

Data sources allowed (owner's call, 2026-11-12): paid licensed APIs; official public sources
(NSE and BSE filings, company investor pages, rating agency notes); and other sites only where
their terms allow automated access. No getting around logins, paywalls or anti-bot blocks, and
no unofficial API wrappers. Collected data carries a lower trust tag and is cross-checked.
Fundamentals vendor choice is still open.
Runs on a laptop. Universe: Nifty 100.
