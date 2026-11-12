from datetime import date

import pytest
from pydantic import ValidationError

from smt.schema import AnalystKind, AnalystReport, Direction, Evidence, Horizon, HorizonView, neutral_views

D = date(2026, 11, 1)


def _report(**over):
    base = dict(analyst_id="x", unit="u", kind=AnalystKind.CODE, symbol="TCS", as_of=D, finding="f", views=neutral_views())
    base.update(over)
    return AnalystReport(**base)


def _views(short=Direction.POSITIVE):
    v = neutral_views()
    v[0] = HorizonView(horizon=Horizon.SHORT, direction=short, strength=0.5, confidence=0.5)
    return v


def test_neutral_report_needs_no_evidence():
    assert _report().finding == "f"


def test_directional_view_needs_evidence():
    with pytest.raises(ValidationError, match="evidence"):
        _report(views=_views())


def test_directional_view_with_evidence_ok():
    ev = Evidence(id="e1", source_type="price", ref="prices/TCS", as_of=D)
    assert _report(views=_views(), evidence=[ev]).evidence[0].id == "e1"


def test_must_cover_all_three_horizons():
    with pytest.raises(ValidationError, match="horizon"):
        _report(views=neutral_views()[:2])


def test_future_evidence_is_rejected():
    ev = Evidence(id="e1", source_type="news", ref="x", as_of=date(2026, 11, 2))
    with pytest.raises(ValidationError, match="look-ahead"):
        _report(evidence=[ev])
