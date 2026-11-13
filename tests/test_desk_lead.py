import re
from datetime import date

import pytest
from pydantic import ValidationError

from smt.desks.lead import check_summary, combine, lead_report, load_weights
from smt.graph import run
from smt.llm import FakeLLM
from smt.schema import AnalystKind, AnalystReport, DeskReport, Direction, Evidence, Horizon, HorizonView, neutral_views
from smt.store import FeatureStore

D = date(2026, 11, 13)
EV = [Evidence(id="e", source_type="price", ref="r", as_of=D)]


def _rep(aid, short=Direction.NEUTRAL, mid=Direction.NEUTRAL, finding=None, gaps=()):
    views = [HorizonView(horizon=Horizon.SHORT, direction=short, strength=0.5, confidence=0.3),
             HorizonView(horizon=Horizon.MID, direction=mid, strength=0.5, confidence=0.3),
             HorizonView(horizon=Horizon.LONG, direction=Direction.NEUTRAL, strength=0, confidence=0)]
    return AnalystReport(analyst_id=aid, unit="market_activity", kind=AnalystKind.CODE, symbol="X", as_of=D,
                         finding=finding or f"{aid} finding with value 1.25.", views=views, evidence=EV,
                         could_not_check=list(gaps))


def _w(**by_id):
    return {"version": "t", "weights": {k: {"short": v, "mid": v, "long": v} for k, v in by_id.items()}}


def _view(views, h):
    return next(v for v in views if v.horizon == h)


def test_zero_weights_keep_the_desk_neutral_and_say_why():
    reps = [_rep("momentum", Direction.POSITIVE, Direction.POSITIVE)]
    views, _, notes = combine(reps, _w(momentum=0.0))
    assert all(v.direction == Direction.NEUTRAL for v in views)
    assert any("no analyst on this desk has a proven edge" in n for n in notes)


def test_weighted_views_combine():
    reps = [_rep("a", Direction.POSITIVE, Direction.POSITIVE), _rep("b", Direction.NEUTRAL, Direction.NEGATIVE)]
    views, _, _ = combine(reps, _w(a=1.0, b=1.0))
    assert _view(views, Horizon.SHORT).direction == Direction.POSITIVE  # (0.15 + 0) / 2 = 0.075
    assert _view(views, Horizon.MID).direction == Direction.NEUTRAL  # (0.15 - 0.15) / 2 = 0


def test_conflicts_are_listed_even_with_zero_weight():
    reps = [_rep("a", Direction.POSITIVE), _rep("b", Direction.NEGATIVE)]
    _, conflicts, _ = combine(reps, _w(a=0.0, b=0.0))
    assert conflicts[0].horizon == Horizon.SHORT and conflicts[0].positive == ["a"] and conflicts[0].negative == ["b"]


def test_market_activity_weights_file_is_valid_and_versioned():
    w = load_weights("market_activity")
    # versions are dated, with .2, .3 ... for a second version on the same day (written by smt.calibrate)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}(\.\d+)?", w["version"]) and w["source"] and set(w["weights"]) == {"price_structure", "momentum", "liquidity", "market_beta"}
    assert all(w["why"][k] for k in w["weights"])
    assert load_weights("no_such_desk")["weights"] == {}


SOURCE = "Facts:\n[momentum] score 1.25 rank 3 of 100.\n[liquidity] Rs 1,637 crore."


@pytest.mark.parametrize("text,problem", [
    ("- Momentum score is 9.9. [momentum]", "number 9.9"),
    ("- Momentum looks fine.", "no [analyst] citation"),
    ("- Fine. [ghost]", "unknown citation"),
    ("- You should buy it. [momentum]", "advice word"),
    ("Momentum is fine. [momentum]", "not a bullet"),
])
def test_bad_summaries_are_caught(text, problem):
    assert any(problem in p for p in check_summary(text, SOURCE, {"momentum", "liquidity", "desk"}))


def test_good_summary_passes():
    text = "- Momentum ranks 3 of 100 with a score of 1.25. [momentum]\n- About Rs 1,637 crore trades daily. [liquidity]"
    assert check_summary(text, SOURCE, {"momentum", "liquidity", "desk"}) == []


def test_llm_summary_used_when_it_passes():
    store = FeatureStore()
    reps = [_rep("momentum", finding="Score 1.25.")]
    llm = FakeLLM(store, {"desk_summary:market_activity": "- Momentum score is 1.25. [momentum]"})
    d = lead_report("market_activity", "X", D, reps, llm)
    assert d.summary_source == "llm" and d.summary_rejected == [] and store.count_llm_calls() == 1


def test_bad_llm_summary_falls_back_to_template():
    reps = [_rep("momentum", finding="Score 1.25.")]
    llm = FakeLLM(FeatureStore(), {"desk_summary:market_activity": "- Strong buy, score 7. [momentum]"})
    d = lead_report("market_activity", "X", D, reps, llm)
    assert d.summary_source == "template" and d.summary_rejected
    assert "[momentum]" in d.summary and "Score 1.25." in d.summary


def test_template_summary_passes_its_own_check():
    reps = [_rep("momentum", Direction.POSITIVE, finding="Rank 3 of 100, score 1.25."),
            _rep("price_structure", Direction.NEGATIVE, finding="Close 1335.40.", gaps=["x"])]
    d = lead_report("market_activity", "X", D, reps)
    src = "\n".join(d.facts + d.notes + ["3-5 months 6-12 months 2 years"])
    assert check_summary(d.summary, src, set(d.analysts) | {"desk"}) == []
    assert d.conflicts and d.data_gaps == ["x"] and d.weights_version == load_weights("market_activity")["version"]


def test_desk_report_needs_all_horizons():
    with pytest.raises(ValidationError):
        DeskReport(unit="u", symbol="X", as_of=D, views=neutral_views()[:1], weights_version="t", summary="-",
                   summary_source="template")


def test_graph_returns_a_desk_report_per_desk():
    store = FeatureStore()
    state = run("x", D, store, desks={"market_activity": [lambda ctx: _rep("momentum", Direction.POSITIVE)]})
    [desk] = state["desk_reports"]
    assert desk.unit == "market_activity" and desk.analysts == ["momentum"]
    assert all(v.direction == Direction.NEUTRAL for v in desk.views)  # momentum weight is 0
    assert store.count_desk_reports(state["run_id"]) == 1
