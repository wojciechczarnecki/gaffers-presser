"""presser log

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-09 00:00:01.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "presser",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("season", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("league_fpl_id", sa.Integer(), nullable=False),
        sa.Column("gameweek_fpl_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("facts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("text", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("model", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("prompt_version", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("latency_seconds", sa.Float(), nullable=True),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("error_class", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("delivery_log_id", sa.Integer(), nullable=True),
        sa.Column("trace_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["season"], ["season.label"]),
        sa.ForeignKeyConstraint(["season", "league_fpl_id"], ["league.season", "league.fpl_id"]),
        sa.ForeignKeyConstraint(
            ["season", "gameweek_fpl_id"], ["gameweek.season", "gameweek.fpl_id"]
        ),
        sa.ForeignKeyConstraint(["delivery_log_id"], ["delivery_log.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_presser_idempotency_key"), "presser", ["idempotency_key"])
    op.create_index(
        "ix_presser_league_gameweek",
        "presser",
        ["season", "league_fpl_id", "gameweek_fpl_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_presser_league_gameweek", table_name="presser")
    op.drop_index(op.f("ix_presser_idempotency_key"), table_name="presser")
    op.drop_table("presser")
