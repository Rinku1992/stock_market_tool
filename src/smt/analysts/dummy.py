"""Placeholder analysts. They return a neutral report so the graph can run end to end."""

from smt.analysts.base import Analyst, AnalystContext
from smt.schema import AnalystKind, AnalystReport, neutral_views


def make_dummy(analyst_id: str, unit: str, kind: AnalystKind = AnalystKind.CODE) -> Analyst:
    def run(ctx: AnalystContext) -> AnalystReport:
        return AnalystReport(
            analyst_id=analyst_id,
            unit=unit,
            kind=kind,
            symbol=ctx.symbol,
            as_of=ctx.as_of,
            finding="not built yet",
            views=neutral_views(),
            could_not_check=["everything: placeholder analyst"],
        )

    return run
