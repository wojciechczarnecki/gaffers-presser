from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlmodel import Session, select

from app.fpl.deadlines import DEFAULT_MAX_LOOKBACK, window_floor
from app.fpl.models.reference import Fixture, Gameweek
from app.retrieval.evaluation.dataset import EvaluationError, WindowRule

MATCH_LENGTH = timedelta(hours=2)


@dataclass(frozen=True)
class GameweekWindow:
    season: str
    gameweek: int
    deadline_at: datetime
    window_start: datetime
    window_end: datetime
    rule: WindowRule
    previous_last_match_end: datetime | None


def gameweek_window(
    session: Session, season: str, gameweek: int, rule: WindowRule
) -> GameweekWindow:
    deadline = session.exec(
        select(Gameweek.deadline_at).where(Gameweek.season == season, Gameweek.fpl_id == gameweek)
    ).first()
    if deadline is None:
        raise EvaluationError(f"gameweek {gameweek} of {season} is not in the database")
    previous_deadline = session.exec(
        select(func.max(Gameweek.deadline_at)).where(
            Gameweek.season == season, Gameweek.deadline_at < deadline
        )
    ).one()
    last_kickoff = session.exec(
        select(func.max(Fixture.kickoff_at)).where(
            Fixture.season == season, Fixture.gameweek_fpl_id == gameweek - 1
        )
    ).one()
    previous_end = last_kickoff + MATCH_LENGTH if last_kickoff is not None else None
    if rule == "after-last-match":
        if previous_end is None:
            raise EvaluationError(
                f"gameweek {gameweek - 1} has no fixture with a kickoff time; use --window alerts"
            )
        start = previous_end
    else:
        start = window_floor(previous_deadline, deadline, DEFAULT_MAX_LOOKBACK, deadline)
    return GameweekWindow(season, gameweek, deadline, start, deadline, rule, previous_end)
