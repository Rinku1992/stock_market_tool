"""Usage: python -m smt.cli RELIANCE [YYYY-MM-DD]"""

import os
import sys
from datetime import date

from smt.graph import run
from smt.store import FeatureStore


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    as_of = date.fromisoformat(argv[2]) if len(argv) > 2 else date.today()
    store = FeatureStore(os.environ.get("SMT_DB_PATH", "data/smt.duckdb"))
    state = run(argv[1], as_of, store)
    print(state["verdict"].model_dump_json(indent=2))
    print(f"\n{len(state.get('reports', []))} analyst reports saved (run {state['run_id']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
