"""match check

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-25 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "extraction_event",
        sa.Column("for_next_pl_match", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("extraction_event", "for_next_pl_match")
