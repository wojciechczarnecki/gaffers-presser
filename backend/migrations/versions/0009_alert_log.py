"""alert log

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-02 00:00:01.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "alert",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("deadline_key", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("slot_minutes", sa.Integer(), nullable=True),
        sa.Column("trigger_x_id", sa.BigInteger(), nullable=True),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("delivery_log_id", sa.Integer(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["trigger_x_id"], ["tweet.x_id"]),
        sa.ForeignKeyConstraint(["delivery_log_id"], ["delivery_log.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key", name="uq_alert_key"),
    )
    op.create_index(
        "ix_alert_deadline_key_recorded_at", "alert", ["deadline_key", "recorded_at"], unique=False
    )

    op.create_table(
        "alert_post",
        sa.Column("alert_id", sa.Integer(), nullable=False),
        sa.Column("player_season", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("player_fpl_id", sa.Integer(), nullable=False),
        sa.Column("tweet_x_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("freshness", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.ForeignKeyConstraint(["alert_id"], ["alert.id"]),
        sa.ForeignKeyConstraint(
            ["player_season", "player_fpl_id"], ["player.season", "player.fpl_id"]
        ),
        sa.ForeignKeyConstraint(["tweet_x_id"], ["tweet.x_id"]),
        sa.PrimaryKeyConstraint("alert_id", "player_season", "player_fpl_id", "tweet_x_id"),
    )
    op.create_index("ix_alert_post_tweet_x_id", "alert_post", ["tweet_x_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_alert_post_tweet_x_id", table_name="alert_post")
    op.drop_table("alert_post")
    op.drop_index("ix_alert_deadline_key_recorded_at", table_name="alert")
    op.drop_table("alert")
