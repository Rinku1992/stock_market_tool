"""The orchestrator. This is the ONLY file allowed to import langgraph.

Fixed path, fixed branches. No LLM decides which analyst runs next.

    data office -> (grade C: refuse) -> router -> desks in parallel -> review -> verdict

Each desk runs its analysts, then its desk lead turns their reports into one DeskReport.
"""

import operator
import uuid
from datetime import date
from typing import Annotated, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from smt.analysts import DESKS
from smt.analysts.base import Analyst, AnalystContext
from smt.data.lake import Lake
from smt.desks.lead import lead_report
from smt.llm import LLMClient
from smt.schema import AnalystReport, Call, DataGrade, DeskReport, Horizon, HorizonVerdict, Verdict
from smt.store import FeatureStore


class RunState(TypedDict, total=False):
    run_id: str
    symbol: str
    instrument_id: str | None
    as_of: date
    data_grade: DataGrade
    stock_type: str
    reports: Annotated[list[AnalystReport], operator.add]
    desk_reports: Annotated[list[DeskReport], operator.add]
    verdict: Verdict


GradeFn = Callable[[AnalystContext], DataGrade]
RouterFn = Callable[[AnalystContext], str]


def _default_grade(ctx: AnalystContext) -> DataGrade:
    return DataGrade.B  # placeholder until the data office is built (phase 1)


def _default_router(ctx: AnalystContext) -> str:
    return "unknown"  # placeholder until the stock type router is built (phase 2)


def build_graph(
    store: FeatureStore,
    llm: LLMClient | None = None,
    lake: Lake | None = None,
    desks: dict[str, list[Analyst]] | None = None,
    grade_fn: GradeFn = _default_grade,
    router_fn: RouterFn = _default_router,
):
    desks = desks if desks is not None else DESKS

    def ctx_of(state: RunState) -> AnalystContext:
        return AnalystContext(
            symbol=state["symbol"],
            as_of=state["as_of"],
            store=store,
            llm=llm,
            stock_type=state.get("stock_type"),
            lake=lake,
            instrument_id=state.get("instrument_id"),
        )

    def data_office(state: RunState) -> dict:
        return {"data_grade": grade_fn(ctx_of(state))}

    def after_data_office(state: RunState) -> str:
        return "refuse" if state["data_grade"] == DataGrade.C else "router"

    def refuse(state: RunState) -> dict:
        return {
            "verdict": Verdict(
                symbol=state["symbol"],
                as_of=state["as_of"],
                data_grade=state["data_grade"],
                refused_reason="data grade C: not enough clean data to give a verdict",
            )
        }

    def router(state: RunState) -> dict:
        return {"stock_type": router_fn(ctx_of(state))}

    def make_desk(unit: str, analysts: list[Analyst]):
        def desk(state: RunState) -> dict:
            ctx = ctx_of(state)
            out = [a(ctx) for a in analysts]
            for r in out:
                store.save_report(r, state["run_id"])
            lead = lead_report(unit, state["symbol"], state["as_of"], out, llm)
            store.save_desk_report(lead, state["run_id"])
            return {"reports": out, "desk_reports": [lead]}

        return desk

    def review(state: RunState) -> dict:
        return {}  # placeholder until the independent review unit is built (phase 5)

    def verdict(state: RunState) -> dict:
        # placeholder until the scoring engine is built (phase 3): always "no call"
        return {
            "verdict": Verdict(
                symbol=state["symbol"],
                as_of=state["as_of"],
                data_grade=state["data_grade"],
                stock_type=state.get("stock_type"),
                horizons=[
                    HorizonVerdict(horizon=h, call=Call.NO_CALL, confidence=0.0, reasons=["scoring engine not built yet"])
                    for h in Horizon
                ],
            )
        }

    g = StateGraph(RunState)
    g.add_node("data_office", data_office)
    g.add_node("refuse", refuse)
    g.add_node("router", router)
    g.add_node("review", review)
    g.add_node("verdict", verdict)
    g.add_edge(START, "data_office")
    g.add_conditional_edges("data_office", after_data_office, {"refuse": "refuse", "router": "router"})
    g.add_edge("refuse", END)
    desk_nodes = []
    for unit, analysts in desks.items():
        node = f"desk_{unit}"
        g.add_node(node, make_desk(unit, analysts))
        g.add_edge("router", node)
        desk_nodes.append(node)
    g.add_edge(desk_nodes, "review")
    g.add_edge("review", "verdict")
    g.add_edge("verdict", END)
    return g.compile()


def run(symbol: str, as_of: date, store: FeatureStore, llm: LLMClient | None = None, lake: Lake | None = None,
        instrument_id: str | None = None, **kwargs) -> RunState:
    graph = build_graph(store, llm, lake, **kwargs)
    return graph.invoke({"run_id": uuid.uuid4().hex, "symbol": symbol.upper(), "instrument_id": instrument_id,
                         "as_of": as_of, "reports": [], "desk_reports": []})
