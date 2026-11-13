"""Registry: which analysts sit in which research unit.

Real analysts replace the dummies here, one at a time.
"""

from smt.analysts.base import Analyst
from smt.analysts.dummy import make_dummy
from smt.analysts.equity_research import financial_trend
from smt.analysts.market_activity import liquidity, market_beta, momentum, price_structure
from smt.schema import AnalystKind

DESKS: dict[str, list[Analyst]] = {
    "equity_research": [financial_trend],
    "market_activity": [price_structure, momentum, liquidity, market_beta],
    "market_news": [make_dummy("event_clusterer", "market_news", AnalystKind.BOTH)],
    "industry_geo": [make_dummy("sector_cycle", "industry_geo", AnalystKind.BOTH)],
    "event_catalyst": [make_dummy("calendar", "event_catalyst")],
}
