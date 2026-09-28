from datetime import datetime

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

from app.fpl.models.columns import utc_column


class JobRun(SQLModel, table=True):
    __tablename__ = "job_run"
    __table_args__ = (
        Index(
            "ix_job_run_job_season_gameweek_started",
            "job",
            "season",
            "gameweek_fpl_id",
            "started_at",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    job: str  # reference_sync | deadline_snapshot | results_sync | league_sync
    season: str | None = None  # no FK: a failed first reference sync has no season
    gameweek_fpl_id: int | None = None
    started_at: datetime = Field(sa_column=utc_column())
    finished_at: datetime = Field(sa_column=utc_column())
    outcome: str  # succeeded | failed
    error_class: str | None = None
