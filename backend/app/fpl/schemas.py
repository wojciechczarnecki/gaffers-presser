from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Generic, TypeVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, TypeAdapter, ValidationError

from app.fpl.errors import PayloadError

T = TypeVar("T")


@dataclass(frozen=True)
class Fetched(Generic[T]):
    data: T
    raw: Any


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Event(StrictModel):
    id: int
    name: str
    deadline_time: AwareDatetime
    finished: bool
    data_checked: bool


class Team(StrictModel):
    id: int
    name: str
    short_name: str


class Element(StrictModel):
    id: int
    web_name: str
    first_name: str
    second_name: str
    team: int
    element_type: int
    status: str
    news: str
    news_added: AwareDatetime | None
    chance_of_playing_this_round: int | None
    chance_of_playing_next_round: int | None
    selected_by_percent: Decimal
    now_cost: int


class Bootstrap(StrictModel):
    events: list[Event]
    teams: list[Team]
    elements: list[Element]


class Fixture(StrictModel):
    id: int
    event: int | None
    kickoff_time: AwareDatetime | None
    team_h: int
    team_a: int
    team_h_score: int | None
    team_a_score: int | None
    finished: bool


class LiveStats(StrictModel):
    starts: int
    minutes: int
    total_points: int


class LiveElement(StrictModel):
    id: int
    stats: LiveStats
    explain: list[dict[str, Any]]


class Live(StrictModel):
    elements: list[LiveElement]


class StandingsEntry(StrictModel):
    entry: int
    entry_name: str
    player_name: str
    rank: int
    event_total: int
    total: int


class StandingsBody(StrictModel):
    has_next: bool
    page: int
    results: list[StandingsEntry]


class LeagueInfo(StrictModel):
    id: int
    name: str


class StandingsPage(StrictModel):
    league: LeagueInfo
    standings: StandingsBody


class EntryHistory(StrictModel):
    points: int
    total_points: int
    event_transfers: int
    event_transfers_cost: int
    points_on_bench: int
    bank: int
    value: int
    overall_rank: int


class Pick(StrictModel):
    element: int
    position: int
    multiplier: int
    is_captain: bool
    is_vice_captain: bool


class AutomaticSub(StrictModel):
    element_in: int
    element_out: int


class Picks(StrictModel):
    active_chip: str | None
    automatic_subs: list[AutomaticSub]
    entry_history: EntryHistory
    picks: list[Pick]


class Chip(StrictModel):
    name: str
    time: AwareDatetime
    event: int


class History(StrictModel):
    chips: list[Chip]


class Transfer(StrictModel):
    element_in: int
    element_in_cost: int
    element_out: int
    element_out_cost: int
    event: int
    time: AwareDatetime


def _field_name(exc: ValidationError) -> str:
    loc = exc.errors()[0]["loc"]
    return ".".join(str(part) for part in loc)


def parse(model: type[T], endpoint_template: str, raw: Any) -> T:
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise PayloadError(endpoint_template, _field_name(exc)) from exc


def parse_list(model: type[T], endpoint_template: str, raw: Any) -> list[T]:
    try:
        return TypeAdapter(list[model]).validate_python(raw)
    except ValidationError as exc:
        raise PayloadError(endpoint_template, _field_name(exc)) from exc
