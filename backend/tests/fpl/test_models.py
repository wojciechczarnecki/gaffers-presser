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
    # SQLModel.metadata is process-wide, so other business modules (e.g. app.worker)
    # may add their own tables to it; this test checks only the FPL domain's tables.
    names = set(SQLModel.metadata.tables.keys())
    assert EXPECTED_TABLE_NAMES <= names


def test_every_datetime_column_is_timezone_aware():
    for name, table in SQLModel.metadata.tables.items():
        if name not in EXPECTED_TABLE_NAMES:
            continue
        for column in table.columns:
            if isinstance(column.type, DateTime):
                assert column.type.timezone is True, f"{table.name}.{column.name}"


def test_every_table_is_keyed_or_linked_by_season():
    for name, table in SQLModel.metadata.tables.items():
        if name not in EXPECTED_TABLE_NAMES:
            continue
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


def test_always_present_payload_columns_are_not_null():
    tables = SQLModel.metadata.tables
    assert tables["deadline_snapshot_player"].c.selected_by_percent.nullable is False
    assert tables["raw_payload"].c.payload.nullable is False
    assert tables["player_gameweek_result"].c.explain.nullable is False
