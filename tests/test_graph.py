from datetime import date
from pathlib import Path

from smt.analysts import DESKS
from smt.graph import run
from smt.schema import Call, DataGrade
from smt.store import FeatureStore

D = date(2026, 11, 1)


def test_full_run_gives_no_call_for_each_horizon():
    store = FeatureStore()
    state = run("reliance", D, store)
    v = state["verdict"]
    assert v.symbol == "RELIANCE"
    assert [h.call for h in v.horizons] == [Call.NO_CALL] * 3
    n_analysts = sum(len(a) for a in DESKS.values())
    assert len(state["reports"]) == n_analysts
    assert store.count_reports(state["run_id"]) == n_analysts


def test_grade_c_refuses_and_runs_no_analyst():
    store = FeatureStore()
    state = run("XYZ", D, store, grade_fn=lambda ctx: DataGrade.C)
    assert state["verdict"].refused_reason
    assert state["verdict"].horizons == []
    assert state["reports"] == []


def test_router_output_reaches_analysts():
    seen = []

    def spy(ctx):
        seen.append(ctx.stock_type)
        return DESKS["equity_research"][0](ctx)

    run("HDFCBANK", D, FeatureStore(), desks={"equity_research": [spy]}, router_fn=lambda ctx: "bank")
    assert seen == ["bank"]


def test_only_graph_file_imports_langgraph():
    src = Path(__file__).parent.parent / "src" / "smt"
    offenders = [p.name for p in src.rglob("*.py") if p.name != "graph.py" and "langgraph" in p.read_text()]
    assert offenders == []
