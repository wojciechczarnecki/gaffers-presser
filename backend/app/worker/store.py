from sqlalchemy import Engine, text
from sqlmodel import Session

from app.fpl.calendar import current_season, season_gameweeks
from app.worker.models import JobRun
from app.worker.schedule import Job, RunRecord, ScheduleState


def _run_records(
    session: Session, season: str | None, only_success: bool
) -> dict[tuple[Job, int | None], RunRecord]:
    query = (
        "SELECT DISTINCT ON (job, gameweek_fpl_id) job, gameweek_fpl_id, started_at,"
        " finished_at, outcome FROM job_run"
        " WHERE ((job = 'reference_sync' AND gameweek_fpl_id IS NULL) OR season = :season)"
    )
    if only_success:
        query += " AND outcome = 'succeeded'"
    query += " ORDER BY job, gameweek_fpl_id, started_at DESC, id DESC"
    rows = session.execute(text(query), {"season": season}).all()

    result: dict[tuple[Job, int | None], RunRecord] = {}
    for row in rows:
        job = Job(row.job)
        result[(job, row.gameweek_fpl_id)] = RunRecord(
            job=job,
            gameweek=row.gameweek_fpl_id,
            started_at=row.started_at,
            finished_at=row.finished_at,
            outcome=row.outcome,
        )
    return result


def load_state(engine: Engine) -> ScheduleState:
    with Session(engine) as session:
        season = current_season(session)
        gameweeks = season_gameweeks(session, season) if season is not None else []
        latest = _run_records(session, season, only_success=False)
        latest_success = _run_records(session, season, only_success=True)
    return ScheduleState(
        season=season, gameweeks=gameweeks, latest=latest, latest_success=latest_success
    )


def latest_runs_by_job(engine: Engine) -> dict[Job, JobRun | None]:
    result: dict[Job, JobRun | None] = {job: None for job in Job}
    with Session(engine) as session:
        rows = session.execute(
            text("SELECT DISTINCT ON (job) * FROM job_run ORDER BY job, started_at DESC, id DESC")
        ).mappings()
        for row in rows:
            result[Job(row["job"])] = JobRun(**dict(row))
    return result
