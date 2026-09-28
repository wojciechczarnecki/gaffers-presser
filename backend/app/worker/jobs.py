import logging
import threading
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from app.db.locks import acquire_job_lock
from app.fpl.client import FplClient
from app.fpl.leagues import sync_leagues
from app.fpl.reference import sync_reference
from app.fpl.results import sync_results
from app.fpl.snapshot import take_deadline_snapshot
from app.worker.models import JobRun
from app.worker.schedule import Job, PlannedAction, RunRecord

logger = logging.getLogger(__name__)


class Shutdown(BaseException):
    pass


def _dispatch(
    session: Session,
    client: FplClient,
    league_ids: list[int],
    action: PlannedAction,
    now: datetime,
) -> str | None:
    if action.job == Job.reference_sync:
        return sync_reference(session, client, now)
    if action.job == Job.deadline_snapshot:
        take_deadline_snapshot(session, client, action.gameweek, now)
        return action.season
    if action.job == Job.results_sync:
        sync_results(session, client, action.gameweek, now)
        return action.season
    if action.job == Job.league_sync:
        sync_leagues(session, client, league_ids, [action.gameweek], now)
        return action.season
    raise AssertionError(f"unknown job {action.job}")  # pragma: no cover


def _write_log_row(
    engine: Engine,
    action: PlannedAction,
    season: str | None,
    started_at: datetime,
    finished_at: datetime,
    outcome: str,
    error_class: str | None,
) -> None:
    try:
        with Session(engine) as session, session.begin():
            session.add(
                JobRun(
                    job=action.job.value,
                    season=season,
                    gameweek_fpl_id=action.gameweek,
                    started_at=started_at,
                    finished_at=finished_at,
                    outcome=outcome,
                    error_class=error_class,
                )
            )
    except SQLAlchemyError as exc:
        logger.error("job run log write failed: %s", type(exc).__name__)


def run_job(
    engine: Engine,
    client: FplClient,
    league_ids: list[int],
    action: PlannedAction,
    now_fn: Callable[[], datetime],
    stop_event: threading.Event,
) -> RunRecord:
    gw_label = action.gameweek if action.gameweek is not None else "-"
    logger.info("job started: job=%s gameweek=%s", action.job.value, gw_label)

    started_at = now_fn()
    season = action.season
    outcome = "succeeded"
    error_class: str | None = None
    try:
        with Session(engine) as session, session.begin():
            acquire_job_lock(session)
            # The job's `now` (and its deadline guard) must not predate the lock wait.
            started_at = now_fn()
            season = _dispatch(session, client, league_ids, action, started_at)
    except Exception as exc:
        if stop_event.is_set():
            raise Shutdown from exc
        outcome = "failed"
        error_class = type(exc).__name__
        season = action.season

    finished_at = now_fn()
    _write_log_row(engine, action, season, started_at, finished_at, outcome, error_class)

    duration = (finished_at - started_at).total_seconds()
    if error_class is None:
        logger.info(
            "job finished: job=%s gameweek=%s outcome=%s duration=%ss",
            action.job.value,
            gw_label,
            outcome,
            duration,
        )
    else:
        logger.info(
            "job finished: job=%s gameweek=%s outcome=%s duration=%ss error=%s",
            action.job.value,
            gw_label,
            outcome,
            duration,
            error_class,
        )

    return RunRecord(
        job=action.job,
        gameweek=action.gameweek,
        started_at=started_at,
        finished_at=finished_at,
        outcome=outcome,
    )
