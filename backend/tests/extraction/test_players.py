from app.extraction.linking import load_players
from app.fpl.models.reference import Player, Season, Team


def _seed(session, season: str, fpl_id: int, web_name: str) -> None:
    session.add(Season(label=season))
    session.add(Team(season=season, fpl_id=1, name="Team One", short_name="ONE"))
    session.flush()
    session.add(
        Player(
            season=season,
            fpl_id=fpl_id,
            web_name=web_name,
            first_name="First",
            second_name="Last",
            team_fpl_id=1,
            position=1,
        )
    )
    session.commit()


def test_only_latest_season(db_session):
    _seed(db_session, "2025/26", fpl_id=1, web_name="Old Season Player")
    _seed(db_session, "2026/27", fpl_id=2, web_name="Current Season Player")

    players, teams = load_players(db_session)

    assert {p.web_name for p in players} == {"Current Season Player"}
    assert {t.season for t in teams} == {"2026/27"}


def test_no_players_gives_empty_index(db_session):
    players, teams = load_players(db_session)
    assert players == []
    assert teams == []
