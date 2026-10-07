"""off-list posts

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-07 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tweet",
        sa.Column("embedded", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("tweet", sa.Column("quoted_x_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_tweet_quoted_x_id", "tweet", ["quoted_x_id"], unique=False)
    op.execute(
        "UPDATE tweet SET quoted_x_id = (raw #>> '{quotedTweet,id}')::bigint"
        " WHERE raw #>> '{quotedTweet,id}' ~ '^[0-9]+$'"
    )
    op.create_table(
        "list_membership",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("list_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("handles", postgresql.ARRAY(sa.String()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_list_membership_fetched_at", "list_membership", ["fetched_at"])


def downgrade() -> None:
    op.drop_index("ix_list_membership_fetched_at", table_name="list_membership")
    op.drop_table("list_membership")
    op.drop_index("ix_tweet_quoted_x_id", table_name="tweet")
    op.drop_column("tweet", "quoted_x_id")
    op.drop_column("tweet", "embedded")
