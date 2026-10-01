from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.llm.structured import Usage

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
class FlowResult:
    events: list[LinkedEvent]
    usage: Usage
    llm_calls: int
    answered_model: str | None = None
    host: str | None = None
    generation_id: str | None = None
