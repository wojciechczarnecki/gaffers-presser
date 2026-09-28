"""tweets

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-28 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tweet",
        sa.Column("x_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("author_handle", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("text", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("is_repost", sa.Boolean(), nullable=False),
        sa.Column("is_reply", sa.Boolean(), nullable=False),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("x_id"),
    )
    op.create_index("ix_tweet_created_at", "tweet", ["created_at"], unique=False)

    op.create_table(
        "tweet_poll",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("new_posts", sa.Integer(), nullable=False),
        sa.Column("error_class", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("retry_after_seconds", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tweet_poll_source_outcome_started",
        "tweet_poll",
        ["source", "outcome", "started_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_tweet_poll_source_outcome_started", table_name="tweet_poll")
    op.drop_table("tweet_poll")
    op.drop_index("ix_tweet_created_at", table_name="tweet")
    op.drop_table("tweet")
