"""The desk lead: turns a desk's analyst reports into one DeskReport.

What decides (code):
- One view per horizon = weighted mix of the analysts' views. Weights come from a versioned
  weights file per desk (desks/weights/<unit>.json). Each version records the test it came
  from. An analyst with no proven edge has weight 0, so it cannot move the desk view.
- Conflicts (analysts pointing opposite ways) and data gaps are listed, never averaged away.

What explains (LLM, optional):
- A short summary for the owner. Code checks it: every number must appear in the inputs,
  every bullet must cite a real analyst, and no advice words are allowed. If the check fails,
  or there is no LLM, a plain summary built by code is used instead.

How it learns: the weights file is the learning surface. The calibration step (planned) will
re-score each analyst on new outcomes and write a new version. Nothing an LLM says changes
a weight.
"""

import json
import re
from pathlib import Path

from smt.llm import LLMClient
from smt.schema import AnalystReport, Conflict, DeskReport, Direction, Horizon, HorizonView

WEIGHTS_DIR = Path(__file__).parent / "weights"
DIRECTION_BAND = 0.05  # a mixed score smaller than this counts as neutral
BANNED = ("buy", "sell", "target price", "recommend", "should invest", "guarantee")
HORIZON_TEXT = {Horizon.SHORT: "3-5 months", Horizon.MID: "6-12 months", Horizon.LONG: "2 years"}
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
_CITE = re.compile(r"\[([a-z0-9_]+)\]")


def load_weights(unit: str) -> dict:
    path = WEIGHTS_DIR / f"{unit}.json"
    if not path.exists():
        return {"unit": unit, "version": "none", "weights": {}, "why": {}}
    return json.loads(path.read_text())


def _sign(d: Direction) -> int:
    return {Direction.POSITIVE: 1, Direction.NEGATIVE: -1}.get(d, 0)


def combine(reports: list[AnalystReport], weights: dict) -> tuple[list[HorizonView], list[Conflict], list[str]]:
    w_all = weights.get("weights", {})
    views, conflicts, notes = [], [], []
    for h in Horizon:
        num = den = conf = 0.0
        pos, neg = [], []
        for r in reports:
            v = next(x for x in r.views if x.horizon == h)
            if v.direction == Direction.POSITIVE:
                pos.append(r.analyst_id)
            elif v.direction == Direction.NEGATIVE:
                neg.append(r.analyst_id)
            w = float(w_all.get(r.analyst_id, {}).get(h.value, 0.0))
            if w <= 0:
                continue
            den += w
            num += w * _sign(v.direction) * v.strength * v.confidence
            conf += w * v.confidence
        score = num / den if den else 0.0
        direction = (Direction.POSITIVE if score > DIRECTION_BAND else
                     Direction.NEGATIVE if score < -DIRECTION_BAND else Direction.NEUTRAL)
        neutral = direction == Direction.NEUTRAL
        views.append(HorizonView(horizon=h, direction=direction, strength=0.0 if neutral else min(1.0, abs(score)),
                                 confidence=0.0 if neutral else min(1.0, conf / den)))
        if pos and neg:
            conflicts.append(Conflict(horizon=h, positive=pos, negative=neg))
        if den == 0:
            notes.append(f"{HORIZON_TEXT[h]}: no analyst on this desk has a proven edge yet, so the desk view is neutral.")
    return views, conflicts, notes


def _facts(reports: list[AnalystReport]) -> list[str]:
    return [f"[{r.analyst_id}] {r.finding}" for r in reports if r.finding != "not built yet"]


def _desk_line(views: list[HorizonView]) -> str:
    return "Desk view: " + "; ".join(f"{HORIZON_TEXT[v.horizon]} {v.direction.value}" for v in views) + "."


def template_summary(facts: list[str], views: list[HorizonView], conflicts: list[Conflict], notes: list[str]) -> str:
    lines = [f"- {_desk_line(views)} [desk]"]
    lines += [f"- {n} [desk]" for n in notes[:1]]
    lines += [f"- Conflict on {HORIZON_TEXT[c.horizon]}: positive from {', '.join(c.positive)}, "
              f"negative from {', '.join(c.negative)}. [desk]" for c in conflicts]
    lines += [f"- {f[f.index(']') + 2:]} [{f[1:f.index(']')]}]" for f in facts]
    return "\n".join(lines)


def check_summary(summary: str, source: str, allowed_ids: set[str]) -> list[str]:
    """Problems with an LLM summary. Empty list = it passed."""
    problems = []
    norm = lambda s: s.replace(",", "")  # noqa: E731
    have = {norm(n) for n in _NUM.findall(source)}
    for n in _NUM.findall(summary):
        if norm(n) not in have:
            problems.append(f"number {n} is not in the analyst reports")
    bullets = [ln for ln in summary.splitlines() if ln.strip()]
    if not bullets or len(bullets) > 6:
        problems.append("summary must have 1 to 6 bullet lines")
    for ln in bullets:
        if not ln.lstrip().startswith("- "):
            problems.append(f"not a bullet: {ln[:40]!r}")
        cites = _CITE.findall(ln)
        if not cites:
            problems.append(f"bullet has no [analyst] citation: {ln[:40]!r}")
        problems += [f"unknown citation [{c}]" for c in cites if c not in allowed_ids]
    low = summary.lower()
    problems += [f"advice word {w!r} is not allowed" for w in BANNED if re.search(rf"\b{w}\b", low)]
    return problems


def _prompt(unit: str, symbol: str, facts: list[str], views: list[HorizonView], conflicts, notes) -> tuple[str, str]:
    system = (
        f"You are the lead of the {unit.replace('_', ' ')} desk in a stock research system. "
        "Write 3 to 5 short bullet points in simple English for an investor. Rules: "
        "1) Use only the facts given. 2) Every bullet starts with '- ' and ends with the source in square "
        "brackets, either an analyst id from the facts or [desk]. 3) Do not write any number that is not in the "
        "facts. 4) Do not change the desk view, and do not give advice to buy or sell. 5) Mention conflicts and "
        "missing data if there are any."
    )
    user = "\n".join([f"Stock: {symbol}", _desk_line(views), *notes,
                      *[f"Conflict on {HORIZON_TEXT[c.horizon]}: positive {c.positive}, negative {c.negative}" for c in conflicts],
                      "Facts:", *facts])
    return system, user


def lead_report(unit: str, symbol: str, as_of, reports: list[AnalystReport], llm: LLMClient | None = None) -> DeskReport:
    weights = load_weights(unit)
    views, conflicts, notes = combine(reports, weights)
    facts = _facts(reports)
    gaps = sorted({g for r in reports for g in r.could_not_check})
    summary, source_kind, rejected = template_summary(facts, views, conflicts, notes), "template", []
    if llm is not None and facts:
        system, user = _prompt(unit, symbol, facts, views, conflicts, notes)
        try:
            text = llm.complete(task=f"desk_summary:{unit}", prompt=user, system=system, max_tokens=500).strip()
            rejected = check_summary(text, system + "\n" + user, {r.analyst_id for r in reports} | {"desk"})
            if not rejected:
                summary, source_kind = text, "llm"
        except Exception as e:  # an LLM outage must not break the desk
            rejected = [f"LLM call failed: {type(e).__name__}"]
    return DeskReport(
        unit=unit, symbol=symbol, as_of=as_of, views=views, analysts=[r.analyst_id for r in reports],
        facts=facts, conflicts=conflicts, data_gaps=gaps, notes=notes, weights_version=weights["version"],
        summary=summary, summary_source=source_kind, summary_rejected=rejected,
    )
