"""extraction

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-28 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "extraction",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tweet_x_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("provider", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("model", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("prompt_version", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_class", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("latency_seconds", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["tweet_x_id"], ["tweet.x_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_extraction_tweet_status_finished",
        "extraction",
        ["tweet_x_id", "status", "finished_at"],
        unique=False,
    )

    op.create_table(
        "extraction_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("extraction_id", sa.Integer(), nullable=False),
        sa.Column("mention", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("team_mention", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("player_season", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("player_fpl_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("certainty", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(["extraction_id"], ["extraction.id"]),
        sa.ForeignKeyConstraint(
            ["player_season", "player_fpl_id"], ["player.season", "player.fpl_id"]
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_extraction_event_extraction_id", "extraction_event", ["extraction_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_extraction_event_extraction_id", table_name="extraction_event")
    op.drop_table("extraction_event")
    op.drop_index("ix_extraction_tweet_status_finished", table_name="extraction")
    op.drop_table("extraction")
