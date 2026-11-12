"""What an analyst is: a plain function from context to report."""

from dataclasses import dataclass
from datetime import date
from typing import Callable

from smt.llm import LLMClient
from smt.schema import AnalystReport
from smt.store import FeatureStore


@dataclass(frozen=True)
class AnalystContext:
    symbol: str
    as_of: date
    store: FeatureStore
    llm: LLMClient | None = None
    stock_type: str | None = None


Analyst = Callable[[AnalystContext], AnalystReport]
