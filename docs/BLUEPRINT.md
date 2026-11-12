# Blueprint

Source of truth for what we are building. Update this file when the design changes.

## Goal

Type a stock name (or pick a Zerodha holding) and get a verdict for three horizons:
short (3-5 months), mid (6-12 months), long (2 years).

The benchmark is reliability:

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
| 1b. Data office | Real source adapters, corporate action fix, reconciler, data grade | 5 test stocks load with a grade | Next, needs API keys |
| 2. Code desks | Router, financial trend, valuation, price structure, ownership | Each returns a report for the 5 stocks | |
| 3. Engines | Scoring engine, backtest engine | A backtest runs on code features | |
| 4. LLM desks | News, credibility, extraction, forensic reading, input guard | Every claim carries a quote | |
| 5. Review + verdict | Review unit, three horizon committees | One full verdict page | |
| 6. Zerodha | Holdings pull, portfolio desk, sentinel | Holdings show hold/add/trim/exit | |
| 7. App | Web UI | Type a name, get the report | |

## Defaults in use

Paid, licensed data through API keys only. No scrapers and no unofficial wrappers
(this rules out Moneycontrol, which has no official API). Vendor choice is open.
Runs on a laptop. Universe: Nifty 100.
