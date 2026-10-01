"""delivery log

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-01 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "delivery_log",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("text_body", sa.String(), nullable=False),
        sa.Column("html_body", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("provider_message_id", sa.String(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_class", sa.String(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_delivery_log_idempotency_key"),
    )
    op.create_index("ix_delivery_log_requested_at", "delivery_log", ["requested_at"])


def downgrade() -> None:
    op.drop_index("ix_delivery_log_requested_at", table_name="delivery_log")
    op.drop_table("delivery_log")
