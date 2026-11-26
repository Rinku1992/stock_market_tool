"""Registry: which analysts sit in which research unit.

Real analysts replace the dummies here, one at a time.
"""

from smt.analysts.base import Analyst
from smt.analysts.dummy import make_dummy
from smt.analysts.earnings_quality import earnings_quality
from smt.analysts.equity_research import financial_trend
from smt.analysts.market_activity import liquidity, market_beta, momentum, price_structure
from smt.analysts.portfolio import holding_context, overlap, position_size, thesis_tracker
from smt.analysts.reverse_valuation import reverse_valuation
from smt.analysts.valuation import capital_returns, earnings_power, relative_valuation
from smt.schema import AnalystKind

DESKS: dict[str, list[Analyst]] = {
    "equity_research": [financial_trend, earnings_power, earnings_quality, capital_returns, relative_valuation,
                        reverse_valuation],
    "market_activity": [price_structure, momentum, liquidity, market_beta],
    "market_news": [make_dummy("event_clusterer", "market_news", AnalystKind.BOTH)],
    "industry_geo": [make_dummy("sector_cycle", "industry_geo", AnalystKind.BOTH)],
    "event_catalyst": [make_dummy("calendar", "event_catalyst")],
    "portfolio": [position_size, overlap, holding_context, thesis_tracker],
}
