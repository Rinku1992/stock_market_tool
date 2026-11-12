# stock_market_tool

A stock research engine. An orchestrator sends a stock through research units, each with
its own analysts and tools, and returns a verdict for three horizons: 3-5 months,
6-12 months and 2 years.

This is decision support, not investment advice.

- Design: [docs/BLUEPRINT.md](docs/BLUEPRINT.md)
- Data formats and schemas: [docs/DATA_CONTRACT.md](docs/DATA_CONTRACT.md)
- Why things are the way they are: [DECISIONS.md](DECISIONS.md)

## Status

Kite prices load on live data. Three real analysts run (price structure, liquidity, market
beta). All other analysts are placeholders, and the verdict is always "no call" until the
scoring engine and backtest are built. See the status table in the blueprint.

## Setup

    python -m venv .venv && source .venv/bin/activate
    pip install -e ".[dev]"
    cp .env.example .env      # add your ANTHROPIC_API_KEY, never commit .env

## Run

    pytest                    # all tests
    python -m smt.cli RELIANCE --isin INE002A01018

## Kite data (run on your own machine)

Put `KITE_API_KEY` and `KITE_API_SECRET` in `.env`. Never commit them.

    python -m smt.kite_cli login                          # once each day, opens the Zerodha login
    python -m smt.kite_cli prices RELIANCE INE002A01018   # 5 years of daily prices
    python -m smt.kite_cli prices "NIFTY 50" IDX:NIFTY50  # the index, for strength versus market
    python -m smt.kite_cli holdings                       # your holdings as of today

The Kite app's redirect URL must be `http://127.0.0.1:8000/kite/callback`.
The Kite adapter is tested against sample payloads only. It has not yet been run on live data.

## Backtest

    python -m smt.backtest_cli price_structure INE002A01018 [more ISINs ...]

Replays history month by month. Results on one stock are too few to trust.

## Layout

    src/smt/sources/      one module per data vendor (kite.py)

    src/smt/schema.py     report form every analyst returns
    src/smt/store.py      point-in-time feature store (DuckDB) + LLM call recorder
    src/smt/llm.py        the one door for LLM calls
    src/smt/data/         data contract, validation gate, raw + Parquet storage, source adapters
    src/smt/analysts/     analysts: plain functions, one per role
    src/smt/graph.py      orchestrator (the only file that imports LangGraph)
    src/smt/cli.py        command line entry
