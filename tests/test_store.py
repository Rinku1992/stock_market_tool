from datetime import date

import pytest

from smt.llm import FakeLLM
from smt.store import FeatureStore


def _store():
    s = FeatureStore()
    # Q1 result published 20 Jul, later restated on 15 Oct
    s.put_feature(symbol="TCS", name="revenue", value=100.0, period_end=date(2026, 6, 30), known_at=date(2026, 7, 20), origin="code", source="t")
    s.put_feature(symbol="TCS", name="revenue", value=95.0, period_end=date(2026, 6, 30), known_at=date(2026, 10, 15), origin="code", source="t")
    return s


def test_nothing_known_before_publication():
    assert _store().get_feature_asof("TCS", "revenue", date(2026, 7, 10)) is None


def test_original_value_before_restatement():
    assert _store().get_feature_asof("TCS", "revenue", date(2026, 8, 1))["value"] == 100.0


def test_restated_value_after_restatement():
    assert _store().get_feature_asof("TCS", "revenue", date(2026, 11, 1))["value"] == 95.0


def test_origin_must_be_code_or_llm():
    with pytest.raises(ValueError):
        FeatureStore().put_feature(symbol="A", name="n", value=1.0, known_at=date(2026, 1, 1), origin="guess", source="t")


def test_every_llm_call_is_recorded():
    s = FeatureStore()
    llm = FakeLLM(s, {"extract": "ok"})
    assert llm.complete(task="extract", prompt="p") == "ok"
    assert s.count_llm_calls() == 1
