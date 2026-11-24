"""Scoring engine on made-up desk views. Hand check for two desks (thresholds 0.10 score, 0.30 confidence):
A weight 2, positive, strength 0.5, confidence 0.6; B weight 1, negative, strength 0.4, confidence 0.5
score = (2 x 0.5 x 0.6 - 1 x 0.4 x 0.5) / 3 = 0.4 / 3 = +0.133; confidence = (2 x 0.6 + 1 x 0.5) / 3 = 0.567
"""

from datetime import date

import pytest

from smt.desks.lead import load_weights
from smt.graph import run
from smt.schema import Call, DataGrade, DeskReport, Direction, Horizon, HorizonView
from smt.scoring import OPEN_ITEMS, score
from smt.store import FeatureStore

D = date(2026, 11, 23)
UNITS = ["a", "b", "c"]


def desk(unit, direction="neutral", strength=0.0, confidence=0.0, built=True, gaps=()):
    views = [HorizonView(horizon=h, direction=Direction(direction), strength=strength, confidence=confidence) for h in Horizon]
    return DeskReport(unit=unit, symbol="X", as_of=D, views=views, facts=["[x] fact"] if built else [],
                      data_gaps=list(gaps), weights_version="t", summary="s", summary_source="template")


def weights(a=0.0, b=0.0, c=0.0, min_score=0.10, min_conf=0.30):
    w = {"a": a, "b": b, "c": c}
    return {"version": "test-1", "weights": {u: {h.value: x for h in Horizon} for u, x in w.items()},
            "thresholds": {"min_score": min_score, "min_confidence": min_conf}}


def verdict(desks, w, price_why=None):
    return score("X", D, DataGrade.B, "general", desks, UNITS, price_why, w)


def test_shipped_weights_are_all_zero_with_thresholds():
    w = load_weights("verdict")
    assert all(v == 0.0 for c in w["weights"].values() for v in c.values())
    assert w["thresholds"] == {"min_score": 0.10, "min_confidence": 0.30}
    from smt.analysts import DESKS
    assert set(w["weights"]) == set(DESKS)


def test_all_weights_zero_gives_no_call_and_says_why():
    v = verdict([desk("a", "positive", 1.0, 1.0), desk("b"), desk("c")], weights())
    assert [h.call for h in v.horizons] == [Call.NO_CALL] * 3
    assert all(h.confidence == 0.0 for h in v.horizons)
    assert all("every verdict weight is 0 (weights version test-1)" in h.reasons[0] for h in v.horizons)
    assert v.weights_version == "test-1"


def test_consider_by_hand_with_disagreement_listed():
    v = verdict([desk("a", "positive", 0.5, 0.6), desk("b", "negative", 0.4, 0.5), desk("c")], weights(a=2, b=1))
    h = v.horizons[0]
    assert h.call == Call.CONSIDER and h.confidence == pytest.approx(1.7 / 3)
    assert "desks disagree: positive a; negative b" in h.reasons
    assert any(r.startswith("score +0.133, confidence 0.57") for r in h.reasons)


def test_avoid():
    v = verdict([desk("a", "negative", 0.5, 0.6), desk("b"), desk("c")], weights(a=1))
    assert v.horizons[1].call == Call.AVOID and v.horizons[1].confidence == pytest.approx(0.6)  # score -0.30


def test_small_score_is_no_call():
    v = verdict([desk("a", "positive", 0.1, 0.6), desk("b"), desk("c")], weights(a=1))  # score +0.06
    h = v.horizons[0]
    assert h.call == Call.NO_CALL and h.confidence == 0.0 and h.reasons[-1] == "below the call threshold"


def test_low_confidence_is_no_call():
    v = verdict([desk("a", "positive", 1.0, 0.2), desk("b"), desk("c")], weights(a=1))  # score +0.20, confidence 0.2
    assert v.horizons[0].call == Call.NO_CALL


def test_missing_or_unbuilt_weighted_desk_blocks_a_call():
    v = verdict([desk("a", "positive", 1.0, 1.0), desk("c")], weights(a=1, b=1))
    assert v.horizons[0].call == Call.NO_CALL and "desk b (weight 1) is missing" in v.horizons[0].reasons
    v = verdict([desk("a", "positive", 1.0, 1.0), desk("b", built=False), desk("c")], weights(a=1, b=1))
    assert "desk b (weight 1) has no built analyst" in v.horizons[0].reasons


def test_unweighted_unbuilt_desk_does_not_block_but_is_listed():
    v = verdict([desk("a", "positive", 1.0, 1.0), desk("b", built=False), desk("c")], weights(a=1))
    assert v.horizons[0].call == Call.CONSIDER
    assert "desk b: not built yet" in v.not_checked


def test_stale_price_blocks_a_call():
    v = verdict([desk("a", "positive", 1.0, 1.0), desk("b"), desk("c")], weights(a=1),
                price_why="latest stored close is 2026-11-13, more than 7 days before 2026-11-23")
    assert [h.call for h in v.horizons] == [Call.NO_CALL] * 3
    assert "price: latest stored close is 2026-11-13, more than 7 days before 2026-11-23" in v.horizons[0].reasons


def test_not_checked_lists_open_items_and_desk_gaps():
    v = verdict([desk("a", gaps=["financials: none"]), desk("b"), desk("c")], weights())
    assert v.not_checked[:len(OPEN_ITEMS)] == OPEN_ITEMS and "a: financials: none" in v.not_checked


def test_graph_run_gives_no_call_with_reasons():
    state = run("reliance", D, FeatureStore())
    v = state["verdict"]
    assert [h.call for h in v.horizons] == [Call.NO_CALL] * 3
    assert all("every verdict weight is 0" in h.reasons[0] for h in v.horizons)
    assert all("price: no stored price for this stock" in h.reasons for h in v.horizons)
    assert v.weights_version == load_weights("verdict")["version"]
    assert "review: LLM reviewers not built" in v.not_checked
