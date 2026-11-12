# stock_market_tool

A stock research engine. An orchestrator sends a stock through research units, each with
its own analysts and tools, and returns a verdict for three horizons: 3-5 months,
6-12 months and 2 years.

This is decision support, not investment advice.

- Design: [docs/BLUEPRINT.md](docs/BLUEPRINT.md)
- Data formats and schemas: [docs/DATA_CONTRACT.md](docs/DATA_CONTRACT.md)
- Why things are the way they are: [DECISIONS.md](DECISIONS.md)

## Status

Phase 0 (foundation) is done. The graph runs end to end with placeholder analysts and
always answers "no call". Real analysts come next.

## Setup

    python -m venv .venv && source .venv/bin/activate
    pip install -e ".[dev]"
    cp .env.example .env      # add your ANTHROPIC_API_KEY, never commit .env

## Run

    pytest                    # all tests
    python -m smt.cli RELIANCE

## Layout

    src/smt/schema.py     report form every analyst returns
    src/smt/store.py      point-in-time feature store (DuckDB) + LLM call recorder
    src/smt/llm.py        the one door for LLM calls
    src/smt/data/         data contract, validation gate, raw + Parquet storage, source adapters
    src/smt/analysts/     analysts: plain functions, one per role
    src/smt/graph.py      orchestrator (the only file that imports LangGraph)
    src/smt/cli.py        command line entry
