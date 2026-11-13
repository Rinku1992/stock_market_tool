"""The common report form. Every analyst returns an AnalystReport.

Rule enforced here: no directional claim without evidence.
"""

from datetime import date, datetime, timezone
from enum import Enum

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Horizon(str, Enum):
    SHORT = "short"  # 3-5 months
    MID = "mid"  # 6-12 months
    LONG = "long"  # 2 years


class Direction(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class AnalystKind(str, Enum):
    CODE = "code"
    LLM = "llm"
    BOTH = "both"


class DataGrade(str, Enum):
    A = "A"
    B = "B"
    C = "C"  # engine refuses to give a verdict


class Call(str, Enum):
    CONSIDER = "consider"
    AVOID = "avoid"
    NO_CALL = "no_call"


class Evidence(BaseModel):
    id: str
    source_type: str  # filing, price, news, computed, ...
    ref: str  # URL or dataset key
    as_of: date
    quote: str | None = None  # exact text, required for LLM extractions


class HorizonView(BaseModel):
    horizon: Horizon
    direction: Direction
    strength: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)


class AnalystReport(BaseModel):
    analyst_id: str
    unit: str
    kind: AnalystKind
    symbol: str
    as_of: date
    finding: str
    evidence: list[Evidence] = Field(default_factory=list)
    views: list[HorizonView]
    could_not_check: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def _check(self) -> "AnalystReport":
        horizons = [v.horizon for v in self.views]
        if sorted(h.value for h in horizons) != sorted(h.value for h in Horizon):
            raise ValueError("views must hold exactly one entry per horizon")
        directional = any(v.direction != Direction.NEUTRAL for v in self.views)
        if directional and not self.evidence:
            raise ValueError("a directional view needs at least one evidence item")
        if any(e.as_of > self.as_of for e in self.evidence):
            raise ValueError("evidence dated after the report as_of date (look-ahead)")
        return self


class Conflict(BaseModel):
    horizon: Horizon
    positive: list[str]
    negative: list[str]


class DeskReport(BaseModel):
    """What a desk lead hands the orchestrator. Views come from code; the summary is for people."""

    unit: str
    symbol: str
    as_of: date
    views: list[HorizonView]
    analysts: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    weights_version: str
    summary: str
    summary_source: Literal["llm", "template"]
    summary_rejected: list[str] = Field(default_factory=list)  # why an LLM summary was thrown out, if it was

    @model_validator(mode="after")
    def _check(self) -> "DeskReport":
        if sorted(v.horizon.value for v in self.views) != sorted(h.value for h in Horizon):
            raise ValueError("views must hold exactly one entry per horizon")
        return self


class HorizonVerdict(BaseModel):
    horizon: Horizon
    call: Call
    confidence: float = Field(ge=0, le=1)
    reasons: list[str] = Field(default_factory=list)
    kill_criteria: list[str] = Field(default_factory=list)


class Verdict(BaseModel):
    symbol: str
    as_of: date
    data_grade: DataGrade
    stock_type: str | None = None
    horizons: list[HorizonVerdict] = Field(default_factory=list)
    refused_reason: str | None = None


def neutral_views() -> list[HorizonView]:
    return [
        HorizonView(horizon=h, direction=Direction.NEUTRAL, strength=0.0, confidence=0.0)
        for h in Horizon
    ]
