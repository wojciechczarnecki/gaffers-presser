"""match stats

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-11 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LATEST_PAYLOAD = """
    SELECT DISTINCT ON (season, gameweek_fpl_id) season, gameweek_fpl_id, payload
    FROM raw_payload
    WHERE endpoint = {endpoint}
    ORDER BY season, gameweek_fpl_id, fetched_at DESC, id DESC
"""


def upgrade() -> None:
    op.add_column(
        "player_gameweek_result",
        sa.Column("live_stats", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_table(
        "player_fixture_stat",
        sa.Column("season", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("fixture_fpl_id", sa.Integer(), nullable=False),
        sa.Column("player_fpl_id", sa.Integer(), nullable=False),
        sa.Column("identifier", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("side", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["season", "fixture_fpl_id"], ["fixture.season", "fixture.fpl_id"]),
        sa.ForeignKeyConstraint(["season", "player_fpl_id"], ["player.season", "player.fpl_id"]),
        sa.ForeignKeyConstraint(["season"], ["season.label"]),
        sa.PrimaryKeyConstraint("season", "fixture_fpl_id", "player_fpl_id", "identifier"),
    )
    op.execute(
        f"""
        WITH latest AS ({LATEST_PAYLOAD.format(endpoint="'event/' || gameweek_fpl_id || '/live'")})
        UPDATE player_gameweek_result r
        SET live_stats = e.element -> 'stats'
        FROM latest l, jsonb_array_elements(l.payload -> 'elements') AS e(element)
        WHERE r.season = l.season
          AND r.gameweek_fpl_id = l.gameweek_fpl_id
          AND r.player_fpl_id = (e.element ->> 'id')::int
        """
    )
    op.execute(
        f"""
        WITH latest AS ({LATEST_PAYLOAD.format(endpoint="'fixtures'")}),
        stat AS (
            SELECT l.season, l.gameweek_fpl_id, (f ->> 'id')::int AS fixture_fpl_id,
                   s ->> 'identifier' AS identifier, side.key AS side,
                   (entry ->> 'element')::int AS player_fpl_id,
                   (entry ->> 'value')::int AS value
            FROM latest l,
                 jsonb_array_elements(l.payload) AS f,
                 jsonb_array_elements(f -> 'stats') AS s,
                 LATERAL (VALUES ('h', s -> 'h'), ('a', s -> 'a')) AS side(key, entries),
                 jsonb_array_elements(side.entries) AS entry
            WHERE (f ->> 'event')::int = l.gameweek_fpl_id
        )
        INSERT INTO player_fixture_stat
            (season, fixture_fpl_id, player_fpl_id, identifier, side, value)
        SELECT DISTINCT ON (stat.season, stat.fixture_fpl_id, stat.player_fpl_id, stat.identifier)
               stat.season, stat.fixture_fpl_id, stat.player_fpl_id, stat.identifier,
               stat.side, stat.value
        FROM stat
        JOIN fixture ON fixture.season = stat.season AND fixture.fpl_id = stat.fixture_fpl_id
        JOIN player ON player.season = stat.season AND player.fpl_id = stat.player_fpl_id
        """
    )


def downgrade() -> None:
    op.drop_table("player_fixture_stat")
    op.drop_column("player_gameweek_result", "live_stats")
