"""player ownership

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-02 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("player", sa.Column("selected_by_percent", sa.Numeric(5, 1), nullable=True))


def downgrade() -> None:
    op.drop_column("player", "selected_by_percent")
