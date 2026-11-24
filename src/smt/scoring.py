"""Scoring engine: turns desk views into one verdict per horizon. Plain code: no LLM, no LangGraph.

Rules (fixed before use; DECISIONS.md, 23 Nov 2026):
- Weights per desk and horizon come from a versioned file (desks/weights/verdict.json, history/ kept). A
  weight is above 0 only when measured edge supports it. With every weight 0 for a horizon, the call is
  "no call", and the reason says so: a verdict from unproven rules would look like advice.
- Blocks (any one gives "no call"): a desk with weight above 0 whose data domain has grade C (equity research
  needs fundamentals, market activity needs prices); the stock's price is missing or stale (latest close more than 7 days
  before as_of, or older than the Nifty 50's latest close); a desk with weight above 0 is missing or has
  no built analyst.
- Score = sum(w x sign x strength x confidence) / sum(w); confidence = sum(w x confidence) / sum(w), over
  desks with w above 0 (the desk lead's rule, one level up). Desks pointing opposite ways are listed,
  never averaged away silently.
- consider: score >= min_score and confidence >= min_confidence; avoid: score <= -min_score and confidence
  >= min_confidence; else no call. Thresholds are in the same versioned file.
- Every verdict lists its reasons per horizon and what was not checked.
- Review veto (smt.review): a critical re-check fail, or a weighted desk with every analyst set aside, makes every
  horizon "no call" with the first reason "review veto: ...".
"""

from datetime import date

from smt.desks.lead import load_weights
from smt.schema import Call, DataGrade, DeskReport, Direction, Horizon, HorizonVerdict, Verdict

UNIT = "verdict"
DOMAIN = {"equity_research": "fundamentals", "market_activity": "prices"}  # data each desk needs
PRICE_STALE_DAYS = 7
OPEN_ITEMS = [
    "review: LLM reviewers not built",
    "kill criteria not set: the sentinel is not built",
]
_SIGN = {Direction.POSITIVE: 1, Direction.NEGATIVE: -1}


def price_problem(lake, instrument_id: str | None, as_of: date) -> str | None:
    """Why the price blocks a call, or None if it is current enough."""
    if lake is None or not instrument_id:
        return "no stored price for this stock"
    from smt.analysts.market_activity import BENCHMARK
    from smt.data.prices import load_bars

    bars = load_bars(lake, instrument_id, as_of)
    if not bars:
        return "no stored price for this stock"
    last = bars[-1].d
    if (as_of - last).days > PRICE_STALE_DAYS:
        return f"latest stored close is {last}, more than {PRICE_STALE_DAYS} days before {as_of}"
    idx = load_bars(lake, BENCHMARK, as_of)
    if idx and last < idx[-1].d:
        return f"latest stored close is {last}, older than the Nifty 50's latest ({idx[-1].d})"
    return None


def desk_built(d: DeskReport) -> bool:
    """A desk counts as built when at least one analyst reported something (not a stub)."""
    return bool(d.facts)


def score_horizon(h: Horizon, desks: dict[str, DeskReport], weights: dict, price_why: str | None,
                  grades: dict[str, tuple[DataGrade, str]] | None = None) -> HorizonVerdict:
    w_all = {u: float(c.get(h.value, 0.0)) for u, c in weights.get("weights", {}).items()}
    used = {u: w for u, w in w_all.items() if w > 0}
    th = weights.get("thresholds", {})
    min_score, min_conf = float(th.get("min_score", 1.0)), float(th.get("min_confidence", 1.0))
    reasons, blocks = [], []
    if price_why:
        blocks.append(f"price: {price_why}")
    if not used:
        reasons.append(f"no desk has a proven edge for this horizon yet: every verdict weight is 0 (weights version "
                       f"{weights.get('version', 'none')}), so no call is made")
        return HorizonVerdict(horizon=h, call=Call.NO_CALL, confidence=0.0, reasons=reasons + blocks)
    for u in used:
        dom = DOMAIN.get(u)
        if grades and dom in grades and grades[dom][0] == DataGrade.C:
            blocks.append(f"desk {u} (weight {used[u]:g}) needs {dom} data, which has grade C ({grades[dom][1]})")
        if u not in desks:
            blocks.append(f"desk {u} (weight {used[u]:g}) is missing")
        elif not desk_built(desks[u]):
            blocks.append(f"desk {u} (weight {used[u]:g}) has no built analyst")
    if blocks:
        return HorizonVerdict(horizon=h, call=Call.NO_CALL, confidence=0.0, reasons=["call blocked"] + blocks)

    num = den = conf = 0.0
    pos, neg = [], []
    for u, w in used.items():
        v = next(x for x in desks[u].views if x.horizon == h)
        sign = _SIGN.get(v.direction, 0)
        num += w * sign * v.strength * v.confidence
        den += w
        conf += w * v.confidence
        (pos if sign > 0 else neg if sign < 0 else []).append(u)
        reasons.append(f"{u}: {v.direction.value}, strength {v.strength:.2f}, confidence {v.confidence:.2f}, weight {w:g}")
    score, conf = num / den, min(1.0, conf / den)
    if pos and neg:
        reasons.append(f"desks disagree: positive {', '.join(pos)}; negative {', '.join(neg)}")
    reasons.append(f"score {score:+.3f}, confidence {conf:.2f}; a call needs score at least +/-{min_score:g} and "
                   f"confidence at least {min_conf:g} (weights version {weights.get('version', 'none')})")
    if conf >= min_conf and score >= min_score:
        return HorizonVerdict(horizon=h, call=Call.CONSIDER, confidence=conf, reasons=reasons)
    if conf >= min_conf and score <= -min_score:
        return HorizonVerdict(horizon=h, call=Call.AVOID, confidence=conf, reasons=reasons)
    return HorizonVerdict(horizon=h, call=Call.NO_CALL, confidence=0.0, reasons=reasons + ["below the call threshold"])


def review_veto(review, desks: dict[str, DeskReport], weights: dict) -> list[str]:
    """What the review vetoes: critical re-check fails, and any desk with weight above 0 whose analysts were all
    set aside by the evidence auditor."""
    if review is None:
        return []
    veto = list(review.veto)
    for u, cells in weights.get("weights", {}).items():
        if any(float(x) > 0 for x in cells.values()) and u in desks:
            names = desks[u].analysts
            if names and all(n in review.set_aside for n in names):
                veto.append(f"desk {u} has every analyst set aside (no valid source)")
    return veto


def score(symbol: str, as_of: date, data_grade: DataGrade, stock_type: str | None, desk_reports: list[DeskReport],
          expected_units: list[str], price_why: str | None, weights: dict | None = None, review=None,
          grades=None) -> Verdict:
    """grades: the data office result (smt.data_office.Grades) or None."""
    weights = weights if weights is not None else load_weights(UNIT)
    desks = {d.unit: d for d in desk_reports}
    veto = review_veto(review, desks, weights)
    not_checked = list(OPEN_ITEMS)
    for u in expected_units:
        if u not in desks:
            not_checked.append(f"desk {u}: no report")
        elif not desk_built(desks[u]):
            not_checked.append(f"desk {u}: not built yet")
    seen = set()
    for d in desk_reports:
        for g in d.data_gaps:
            item = f"{d.unit}: {g}"
            if item not in seen:
                seen.add(item)
                not_checked.append(item)
    by_domain = ({"prices": (grades.prices, grades.reasons[0]), "fundamentals": (grades.fundamentals, grades.reasons[-1])}
                 if grades is not None and len(grades.reasons) >= 2 else None)
    horizons = [score_horizon(h, desks, weights, price_why, by_domain) for h in Horizon]
    if veto:
        first = "review veto: " + "; ".join(veto)
        horizons = [hv.model_copy(update={"call": Call.NO_CALL, "confidence": 0.0, "reasons": [first] + hv.reasons})
                    for hv in horizons]
    return Verdict(symbol=symbol, as_of=as_of, data_grade=data_grade, stock_type=stock_type, horizons=horizons,
                   weights_version=weights.get("version"), not_checked=not_checked,
                   price_grade=grades.prices if grades is not None else None,
                   fundamentals_grade=grades.fundamentals if grades is not None else None,
                   grade_reasons=list(grades.reasons) if grades is not None else [])
