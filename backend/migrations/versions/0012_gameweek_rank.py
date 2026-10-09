"""gameweek rank

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-09 00:00:02.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("manager_gameweek", sa.Column("gameweek_rank", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("manager_gameweek", "gameweek_rank")
