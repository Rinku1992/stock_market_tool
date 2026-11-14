"""LLM token use, from the llm_calls table.

    python -m smt.llm_usage

Shows tokens per day (India time), per task and per model, cache hits separately, and today's
use against the caps. Never reads or prints any key.
"""

import os
from collections import defaultdict

from smt.llm import DEFAULT_MAX_TOKENS_PER_DAY, DEFAULT_MAX_TOKENS_PER_RUN, _env_int, _today_start_utc
from smt.store import FeatureStore


def summary(rows: list[tuple]) -> dict[str, dict]:
    """rows from FeatureStore.llm_usage() -> totals by day, task and model: [calls, cache hits, input, output]."""
    out = {"day": defaultdict(lambda: [0, 0, 0, 0]), "task": defaultdict(lambda: [0, 0, 0, 0]),
           "model": defaultdict(lambda: [0, 0, 0, 0])}
    for day, task, model, calls, hits, tin, tout in rows:
        for kind, key in (("day", str(day)), ("task", task), ("model", model)):
            t = out[kind][key]
            t[0] += calls; t[1] += hits; t[2] += tin; t[3] += tout
    return out


def _print(title: str, totals: dict) -> None:
    print(f"\n{title:28} {'calls':>6} {'cache hits':>10} {'input':>10} {'output':>10} {'total':>10}")
    for key, (c, h, i, o) in totals.items():
        print(f"{key:28} {c:>6} {h:>10} {i:>10,} {o:>10,} {i + o:>10,}")


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    store = FeatureStore(os.environ.get("SMT_DB_PATH", "data/smt.duckdb"))
    rows = store.llm_usage()
    day_cap = _env_int("SMT_LLM_MAX_TOKENS_PER_DAY", DEFAULT_MAX_TOKENS_PER_DAY)
    run_cap = _env_int("SMT_LLM_MAX_TOKENS_PER_RUN", DEFAULT_MAX_TOKENS_PER_RUN)
    today = store.llm_tokens_since(_today_start_utc())
    print(f"LLM usage (tokens = input + output from the API; cache hits use none)")
    print(f"Today (India time): {today:,} of the day cap {day_cap:,}. Run cap: {run_cap:,}.")
    if not rows:
        print("\nNo LLM calls recorded yet.")
        return
    s = summary(rows)
    _print("per day", s["day"])
    _print("per task", s["task"])
    _print("per model", s["model"])


if __name__ == "__main__":
    main()
