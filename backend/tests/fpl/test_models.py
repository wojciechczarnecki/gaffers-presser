from sqlalchemy import DateTime
from sqlmodel import SQLModel

import app.fpl.models  # noqa: F401

EXPECTED_TABLE_NAMES = {
    "season",
    "gameweek",
    "team",
    "player",
    "fixture",
    "player_flag_change",
    "deadline_snapshot_player",
    "raw_payload",
    "league",
    "manager",
    "league_membership",
    "league_standing",
    "manager_gameweek",
    "manager_pick",
    "manager_auto_sub",
    "manager_transfer",
    "manager_chip",
    "player_gameweek_result",
}

TABLES_WITHOUT_SEASON_FK = {"season"}
TABLES_WITH_SEASON_FK_NOT_IN_PK = {"player_flag_change", "raw_payload"}


def test_table_names_match_schema():
    names = set(SQLModel.metadata.tables.keys())
    assert names == EXPECTED_TABLE_NAMES


def test_every_datetime_column_is_timezone_aware():
    for table in SQLModel.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, DateTime):
                assert column.type.timezone is True, f"{table.name}.{column.name}"


def test_every_table_is_keyed_or_linked_by_season():
    for table in SQLModel.metadata.tables.values():
        if table.name in TABLES_WITHOUT_SEASON_FK:
            continue
        pk_columns = {c.name for c in table.primary_key.columns}
        if "season" in pk_columns:
            continue
        assert table.name in TABLES_WITH_SEASON_FK_NOT_IN_PK
        fk_targets = {
            (fk.column.table.name, fk.column.name)
            for col in table.columns
            for fk in col.foreign_keys
        }
        assert ("season", "label") in fk_targets
