"""LLM score schema (prompt_version v1). Invalid model output is dropped, never repaired."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class EventType(StrEnum):
    earnings = "earnings"
    guidance = "guidance"
    analyst_rating = "analyst_rating"
    mna = "mna"
    regulatory_legal = "regulatory_legal"
    product = "product"
    management = "management"
    capital_return = "capital_return"
    financing = "financing"
    macro_sector = "macro_sector"
    market_move_recap = "market_move_recap"
    other = "other"


class RiskFlag(StrEnum):
    bankruptcy_restructuring = "bankruptcy_restructuring"
    fraud_investigation = "fraud_investigation"
    delisting = "delisting"
    trading_halt = "trading_halt"
    pending_binary_event = "pending_binary_event"
    dilution = "dilution"
    accounting_restatement = "accounting_restatement"
    none = "none"


class SymbolScore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    direction: float = Field(ge=-1, le=1, description="expected price impact, -1 very negative .. +1 very positive")
    magnitude: float = Field(ge=0, le=1, description="size of expected move relative to typical news")
    confidence: float = Field(ge=0, le=1)


class NewsScore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_type: EventType
    is_new_information: bool
    horizon_days: int = Field(ge=1, le=60)
    risk_flags: list[RiskFlag]
    scores: list[SymbolScore]


def json_schema() -> dict:
    """Schema for output_config.format; numeric bounds are enforced by Pydantic afterwards."""
    sym = {
        "type": "object",
        "properties": {
            "symbol": {"type": "string"},
            "direction": {"type": "number"},
            "magnitude": {"type": "number"},
            "confidence": {"type": "number"},
        },
        "required": ["symbol", "direction", "magnitude", "confidence"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "event_type": {"type": "string", "enum": [e.value for e in EventType]},
            "is_new_information": {"type": "boolean"},
            "horizon_days": {"type": "integer"},
            "risk_flags": {"type": "array", "items": {"type": "string", "enum": [r.value for r in RiskFlag]}},
            "scores": {"type": "array", "items": sym},
        },
        "required": ["event_type", "is_new_information", "horizon_days", "risk_flags", "scores"],
        "additionalProperties": False,
    }


def parse(text: str, allowed_symbols: set[str]) -> NewsScore | None:
    try:
        out = NewsScore.model_validate_json(text)
    except ValidationError:
        return None
    if any(s.symbol not in allowed_symbols for s in out.scores):
        return None  # model invented a symbol: drop the whole item
    return out
