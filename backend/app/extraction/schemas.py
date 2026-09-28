from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

EventType = Literal["out", "doubt", "benched", "confirmed_starter"]
Certainty = Literal["confirmed", "likely", "rumour"]


class ExtractedEvent(BaseModel):
    player: str
    team: str | None = None
    event_type: EventType
    certainty: Certainty


class ExtractionOutput(BaseModel):
    events: list[ExtractedEvent]


class Disambiguation(BaseModel):
    fpl_id: int | None = None


@dataclass(frozen=True)
class PostInput:
    x_id: int
    author_handle: str
    text: str
    created_at: datetime
    is_repost: bool
    is_reply: bool


@dataclass(frozen=True)
class LinkedEvent:
    mention: str
    team: str | None
    player_season: str | None
    player_fpl_id: int | None
    event_type: EventType
    certainty: Certainty


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=_add_optional(self.input_tokens, other.input_tokens),
            output_tokens=_add_optional(self.output_tokens, other.output_tokens),
        )


def _add_optional(a: int | None, b: int | None) -> int | None:
    if a is None and b is None:
        return None
    return (a or 0) + (b or 0)


@dataclass(frozen=True)
class FlowResult:
    events: list[LinkedEvent]
    usage: Usage
    llm_calls: int
