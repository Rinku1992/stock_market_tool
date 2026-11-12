# Rules for any Claude session working in this repo

Read `docs/BLUEPRINT.md`, `docs/DATA_CONTRACT.md` and `DECISIONS.md` first. They are the source of truth.

## Hard rules

1. Never read, print, commit or upload `.env`, `data/.kite_token.json`, or any key or token.
2. Never place, modify or cancel orders. Kite is used read-only.
3. Paid, licensed data sources only. No scrapers and no unofficial API wrappers.
4. Only `src/smt/graph.py` may import LangGraph. Analysts are plain functions.
5. All LLM calls go through `src/smt/llm.py`.
6. All data enters through an adapter and the contract in `src/smt/data/contract.py`.
   Never write to `data/lake/canonical` by hand.
7. Code computes numbers. The LLM extracts facts with exact quotes. It never does maths or gives verdicts.
8. Do not state anything about a vendor, an API or a data value that you have not checked. Say "not verified" instead.

## Working rules

- Run `git pull` before starting. Work on a branch, run `pytest`, then merge to `main`.
- Every change needs tests. `pytest` must pass before a push.
- After a push, check that new files are really in git: `git ls-files <path>`.
- Log each design choice in `DECISIONS.md` with the reason.
- Update the status table in `docs/BLUEPRINT.md` when a phase step is done.
- Keep answers to the owner short, in simple English.
- Commands that need the Kite login (`python -m smt.kite_cli ...`) only work on the owner's machine.
