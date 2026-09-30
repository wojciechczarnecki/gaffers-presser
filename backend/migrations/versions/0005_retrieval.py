"""retrieval

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-30 00:00:00.000000

"""

from typing import Sequence, Union

import pgvector.sqlalchemy
import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute("CREATE TEXT SEARCH CONFIGURATION english_unaccent (COPY = english)")
    op.execute(
        "ALTER TEXT SEARCH CONFIGURATION english_unaccent"
        " ALTER MAPPING FOR hword, hword_part, word WITH unaccent, english_stem"
    )
    op.add_column(
        "tweet",
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english_unaccent'::regconfig, text)", persisted=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_tweet_search_vector",
        "tweet",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_table(
        "post_embedding",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tweet_x_id", sa.BigInteger(), nullable=False),
        sa.Column("model", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("status", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.VECTOR(), nullable=True),
        sa.Column("dimensions", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("latency_seconds", sa.Float(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_class", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tweet_x_id"], ["tweet.x_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tweet_x_id", "model", name="uq_post_embedding_tweet_model"),
    )
    op.create_index(
        "ix_post_embedding_model_status", "post_embedding", ["model", "status"], unique=False
    )


def downgrade() -> None:
    # The evaluation schema (app.retrieval.evaluation.schema) depends on english_unaccent and
    # on the vector type; it is rebuilt on every evaluation run, so dropping it loses nothing.
    op.execute("DROP SCHEMA IF EXISTS retrieval_eval CASCADE")
    op.drop_index("ix_post_embedding_model_status", table_name="post_embedding")
    op.drop_table("post_embedding")
    op.drop_index("ix_tweet_search_vector", table_name="tweet", postgresql_using="gin")
    op.drop_column("tweet", "search_vector")
    op.execute("DROP TEXT SEARCH CONFIGURATION english_unaccent")
    op.execute("DROP EXTENSION IF EXISTS unaccent")
    op.execute("DROP EXTENSION IF EXISTS vector")
