"""Owner-entered assumptions for the cost of capital. Versioned, never guessed, never fetched from the web.

The file cost_of_capital.json holds four inputs, each with value (in percent, e.g. 6.45 means 6.45%),
source, source_date and entered_on. Each `python -m smt.assumptions set ...` writes a new version and
copies the old file into history/ (never overwritten), like desks/weights.

As-of rule: a run on date D uses the latest version entered on or before D (no look-ahead).
Stale: gsec_10y older than 45 days at D, the other three older than 400 days (by source_date).
"""

import json
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path

FOLDER = Path(__file__).parent
NAME = "cost_of_capital"
INPUTS = {
    "gsec_10y": "India 10-year government bond yield",
    "mature_erp": "mature market equity risk premium (Damodaran)",
    "india_crp": "India country risk premium (Damodaran)",
    "india_default_spread": "India default spread (Damodaran, same table)",
}
STALE_DAYS = {"gsec_10y": 45, "mature_erp": 400, "india_crp": 400, "india_default_spread": 400}
MIN_PCT, MAX_PCT = 0.3, 25.0  # entered in percent; 0.065 for 6.5% is refused


def empty_doc() -> dict:
    return {"name": NAME, "version": "empty", "entered_on": None, "unit": "percent",
            "inputs": {k: {"value": None, "source": None, "source_date": None, "entered_on": None} for k in INPUTS}}


def _seq(version: str) -> int:
    parts = version.split(".")
    return int(parts[1]) if len(parts) > 1 else 1


def _docs(folder: Path) -> list[dict]:
    paths = [folder / f"{NAME}.json"] + sorted((folder / "history").glob(f"{NAME}_*.json"))
    return [json.loads(p.read_text()) for p in paths if p.exists()]


def load_asof(as_of: date, folder: Path = FOLDER) -> dict | None:
    """The latest version entered on or before as_of, or None."""
    ok = [d for d in _docs(folder) if d.get("entered_on") and date.fromisoformat(d["entered_on"]) <= as_of]
    if not ok:
        return None
    return max(ok, key=lambda d: (d["entered_on"], _seq(d["version"])))


@dataclass
class Input:
    name: str
    value: float  # a fraction, e.g. 0.0645
    source: str
    source_date: date
    stale: bool

    def text(self) -> str:
        s = f"{INPUTS[self.name]} {self.value:.2%} ({self.source}, {self.source_date}"
        return s + (f"; stale: older than {STALE_DAYS[self.name]} days)" if self.stale else ")")


def inputs_asof(as_of: date, folder: Path = FOLDER) -> tuple[dict[str, Input] | None, str | None]:
    """All four inputs as known on as_of, or None and the reason."""
    doc = load_asof(as_of, folder)
    if doc is None:
        return None, f"no cost of capital inputs entered on or before {as_of}"
    out, missing = {}, []
    for k in INPUTS:
        x = doc["inputs"].get(k) or {}
        if x.get("value") is None or not x.get("source") or not x.get("source_date"):
            missing.append(k)
            continue
        sd = date.fromisoformat(x["source_date"])
        out[k] = Input(k, x["value"] / 100, x["source"], sd, (as_of - sd).days > STALE_DAYS[k])
    if missing:
        return None, f"cost of capital inputs not entered: {', '.join(missing)} (version {doc['version']})"
    return out, None


def _next_version(today: date, folder: Path) -> str:
    taken = {d.get("version") for d in _docs(folder)}
    v, i = today.isoformat(), 1
    while (v if i == 1 else f"{v}.{i}") in taken:
        i += 1
    return v if i == 1 else f"{v}.{i}"


def set_input(name: str, value: float, source: str, source_date: date, today: date, folder: Path = FOLDER) -> dict:
    """Write a new version with one input changed. The old file goes to history/."""
    if name not in INPUTS:
        raise ValueError(f"unknown input {name!r}; one of {', '.join(INPUTS)}")
    if not MIN_PCT <= value <= MAX_PCT:
        raise ValueError(f"value {value} looks wrong: enter percent between {MIN_PCT} and {MAX_PCT}, e.g. 6.45 for 6.45%")
    if not source.strip():
        raise ValueError("source is required")
    if source_date > today:
        raise ValueError(f"source date {source_date} is after today {today}")
    path = folder / f"{NAME}.json"
    old = json.loads(path.read_text()) if path.exists() else empty_doc()
    doc = json.loads(json.dumps(old))
    doc["version"] = _next_version(today, folder)
    doc["entered_on"] = today.isoformat()
    doc["inputs"][name] = {"value": value, "source": source.strip(), "source_date": source_date.isoformat(),
                           "entered_on": today.isoformat()}
    if path.exists():
        hist = folder / "history"
        hist.mkdir(parents=True, exist_ok=True)
        target = hist / f"{NAME}_{old.get('version', 'none')}.json"
        if not target.exists():
            shutil.copy2(path, target)
    path.write_text(json.dumps(doc, indent=2) + "\n")
    return doc


# ---------------------------------------------------------------- owner sector map

SECTOR_MAP = "sector_map"


def load_sector_map(folder: Path = FOLDER) -> dict[str, str]:
    """Owner-entered symbol -> sector (current version). Empty when nothing is entered."""
    path = folder / f"{SECTOR_MAP}.json"
    return dict(json.loads(path.read_text()).get("sectors", {})) if path.exists() else {}


def set_sector(symbol: str, sector: str, today: date, folder: Path = FOLDER) -> dict:
    """Write a new sector map version with one symbol set. The old file goes to history/."""
    symbol, sector = symbol.strip().upper(), sector.strip()
    if not symbol or not sector:
        raise ValueError("symbol and sector are both required")
    path = folder / f"{SECTOR_MAP}.json"
    old = json.loads(path.read_text()) if path.exists() else {"name": SECTOR_MAP, "version": "empty", "sectors": {}}
    hist = folder / "history"
    hist.mkdir(parents=True, exist_ok=True)
    taken = {old.get("version")} | {json.loads(p.read_text()).get("version")
                                    for p in hist.glob(f"{SECTOR_MAP}_*.json")}
    v, i = today.isoformat(), 1
    while (v if i == 1 else f"{v}.{i}") in taken:
        i += 1
    doc = {**old, "version": v if i == 1 else f"{v}.{i}", "entered_on": today.isoformat(),
           "sectors": {**old.get("sectors", {}), symbol: sector}}
    if path.exists():
        target = hist / f"{SECTOR_MAP}_{old.get('version', 'none')}.json"
        if not target.exists():
            shutil.copy2(path, target)
    path.write_text(json.dumps(doc, indent=2) + "\n")
    return doc
