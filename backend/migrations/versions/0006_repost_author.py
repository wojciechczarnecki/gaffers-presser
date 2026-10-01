"""repost author

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-30 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tweet", sa.Column("reposted_author_handle", sa.String(), nullable=True))
    op.execute(
        "UPDATE tweet SET reposted_author_handle = COALESCE("
        "raw #>> '{retweetedTweet,user,username}',"
        " raw #>> '{retweeted_tweet,author,userName}',"
        " raw #>> '{retweeted_author,username}')"
        " WHERE is_repost"
    )


def downgrade() -> None:
    op.drop_column("tweet", "reposted_author_handle")
