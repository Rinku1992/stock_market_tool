"""python -m smt.assumptions set <name> --value X --source "..." --source-date YYYY-MM-DD
python -m smt.assumptions show
python -m smt.assumptions sector SYMBOL "Sector name""""

import argparse
from datetime import date, datetime, timedelta, timezone

from smt.assumptions import FOLDER, INPUTS, NAME, inputs_asof, load_asof, set_input, set_sector

IST = timezone(timedelta(hours=5, minutes=30))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="smt.assumptions", description="Owner-entered cost of capital inputs (versioned).")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set", help="enter one input; writes a new version, old one kept in history/")
    s.add_argument("name", choices=list(INPUTS))
    s.add_argument("--value", type=float, required=True, help="in percent, e.g. 6.45 for 6.45%%")
    s.add_argument("--source", required=True, help="where the figure comes from, e.g. a page title and URL")
    s.add_argument("--source-date", required=True, type=date.fromisoformat, help="date of the source figure")
    sub.add_parser("show", help="show the current version and what a run today would use")
    sc = sub.add_parser("sector", help="owner sector for a symbol (used before the index list industry)")
    sc.add_argument("symbol")
    sc.add_argument("sector_name")
    a = p.parse_args(argv)
    today = datetime.now(IST).date()
    if a.cmd == "sector":
        doc = set_sector(a.symbol, a.sector_name, today)
        print(f"Saved sector map version {doc['version']}: {a.symbol.upper()} = {a.sector_name}. "
              f"Old version kept in {FOLDER / 'history'}.")
        return
    if a.cmd == "set":
        doc = set_input(a.name, a.value, a.source, a.source_date, today)
        x = doc["inputs"][a.name]
        print(f"Saved {NAME} version {doc['version']}: {a.name} = {x['value']}% ({x['source']}, {x['source_date']}), "
              f"entered on {x['entered_on']}. Old version kept in {FOLDER / 'history'}.")
        return
    doc = load_asof(today) or {"version": "none entered", "inputs": {}}
    print(f"{NAME} version used today ({today}): {doc['version']}")
    for k, label in INPUTS.items():
        x = doc["inputs"].get(k) or {}
        v = "not entered" if x.get("value") is None else f"{x['value']}%"
        print(f"  {k:22} {v:>12}  {x.get('source') or '-'}  source date {x.get('source_date') or '-'}  "
              f"entered {x.get('entered_on') or '-'}   ({label})")
    _, why = inputs_asof(today)
    for k, i in (inputs_asof(today)[0] or {}).items():
        if i.stale:
            print(f"  stale: {k} source date {i.source_date}")
    print("  ready" if why is None else f"  not ready: {why}")


if __name__ == "__main__":
    main()
