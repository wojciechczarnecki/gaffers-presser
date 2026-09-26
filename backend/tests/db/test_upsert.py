from sqlmodel import select

from app.db.upsert import upsert
from app.fpl.models import Season, Team


def test_insert_and_update_and_idempotent(db_session):
    upsert(db_session, Season, [{"label": "2026/27"}], ["label"])
    upsert(
        db_session,
        Team,
        [{"season": "2026/27", "fpl_id": 1, "name": "Arsenal", "short_name": "ARS"}],
        ["season", "fpl_id"],
    )
    db_session.commit()
    team = db_session.get(Team, ("2026/27", 1))
    assert team.name == "Arsenal"

    upsert(
        db_session,
        Team,
        [{"season": "2026/27", "fpl_id": 1, "name": "Arsenal FC", "short_name": "ARS"}],
        ["season", "fpl_id"],
    )
    db_session.commit()
    db_session.refresh(team)
    assert team.name == "Arsenal FC"

    upsert(
        db_session,
        Team,
        [{"season": "2026/27", "fpl_id": 1, "name": "Arsenal FC", "short_name": "ARS"}],
        ["season", "fpl_id"],
    )
    db_session.commit()
    rows = db_session.exec(select(Team)).all()
    assert len(rows) == 1
    assert rows[0].name == "Arsenal FC"


def test_key_only_table_upsert_does_not_duplicate(db_session):
    upsert(db_session, Season, [{"label": "2026/27"}], ["label"])
    db_session.commit()
    upsert(db_session, Season, [{"label": "2026/27"}], ["label"])
    db_session.commit()
    rows = db_session.exec(select(Season)).all()
    assert len(rows) == 1
