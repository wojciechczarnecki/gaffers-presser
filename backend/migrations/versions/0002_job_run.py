"""job run log

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-27 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "job_run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("season", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("gameweek_fpl_id", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("error_class", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_job_run_job_season_gameweek_started",
        "job_run",
        ["job", "season", "gameweek_fpl_id", "started_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_job_run_job_season_gameweek_started", table_name="job_run")
    op.drop_table("job_run")
