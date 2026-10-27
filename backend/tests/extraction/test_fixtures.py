from datetime import UTC, datetime, timedelta

from app.extraction.fixtures import (
    FixtureIndex,
    FixtureRecord,
    fixtures_snapshot,
    load_fixtures,
    load_fixtures_snapshot,
    render_fixture,
    render_fixtures_context,
)
from app.extraction.linking import PlayerIndex, TeamRecord
from app.fpl.models.reference import Fixture, Season, Team

SEASON = "2026/27"
AT = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
TEAMS = [
    TeamRecord(season=SEASON, fpl_id=1, name="Arsenal", short_name="ARS"),
    TeamRecord(season=SEASON, fpl_id=2, name="Chelsea", short_name="CHE"),
    TeamRecord(season=SEASON, fpl_id=3, name="Fulham", short_name="FUL"),
]


def _fixture(fpl_id, kickoff, home=1, away=2) -> FixtureRecord:
    return FixtureRecord(
        season=SEASON,
        fpl_id=fpl_id,
        gameweek=fpl_id,
        kickoff_at=kickoff,
        team_h_fpl_id=home,
        team_a_fpl_id=away,
    )


def test_around_previous_and_next():
    earlier = _fixture(1, AT - timedelta(days=7), home=2, away=1)
    exactly_now = _fixture(2, AT, home=1, away=3)
    later = _fixture(3, AT + timedelta(days=7), home=1, away=2)
    other_club = _fixture(4, AT + timedelta(days=1), home=2, away=3)
    index = FixtureIndex([later, other_club, exactly_now, earlier])

    assert index.around(1, AT) == (exactly_now, later)  # a kickoff equal to `at` is previous
    assert index.around(1, AT - timedelta(days=1)) == (earlier, exactly_now)
    assert index.next_kickoff(1, AT) == later.kickoff_at
    assert index.around(3, AT) == (exactly_now, other_club)


def test_around_skips_fixtures_without_kickoff():
    index = FixtureIndex([_fixture(1, None), _fixture(2, AT + timedelta(days=1))])

    assert index.around(1, AT) == (None, _fixture(2, AT + timedelta(days=1)))


def test_no_next_fixture_at_season_end():
    last = _fixture(1, AT - timedelta(days=1))
    index = FixtureIndex([last])

    assert index.around(1, AT) == (last, None)
    assert index.next_kickoff(1, AT) is None
    assert index.around(9, AT) == (None, None)


def test_snapshot_round_trip(tmp_path):
    fixtures = [_fixture(2, AT), _fixture(1, None), _fixture(3, AT + timedelta(days=1))]
    path = tmp_path / "fixtures.json"
    import json

    path.write_text(json.dumps(fixtures_snapshot(fixtures)))

    assert load_fixtures_snapshot(path) == sorted(fixtures, key=lambda f: f.fpl_id)
    assert json.loads(path.read_text())["season"] == SEASON


def test_load_fixtures_reads_the_current_season(db_session):
    for season in ("2025/26", SEASON):
        db_session.add(Season(label=season))
        db_session.flush()
        db_session.add(Team(season=season, fpl_id=1, name="A", short_name="AAA"))
        db_session.add(Team(season=season, fpl_id=2, name="B", short_name="BBB"))
        db_session.flush()
        db_session.add(
            Fixture(
                season=season,
                fpl_id=1,
                kickoff_at=AT,
                team_h_fpl_id=1,
                team_a_fpl_id=2,
                finished=False,
            )
        )
    db_session.commit()

    [record] = load_fixtures(db_session)

    assert record.season == SEASON
    assert record.kickoff_at == AT


def test_render_fixture_home_and_away():
    index = PlayerIndex([], TEAMS)
    fixture = _fixture(1, datetime(2026, 10, 17, 14, 0, tzinfo=UTC), home=1, away=2)

    assert render_fixture(fixture, 1, index) == "v Chelsea (H), 2026-10-17T14:00Z"
    assert render_fixture(fixture, 2, index) == "v Arsenal (A), 2026-10-17T14:00Z"


def test_render_fixtures_context_one_line_per_club():
    index = PlayerIndex([], TEAMS)
    fixtures = FixtureIndex(
        [
            _fixture(1, AT - timedelta(days=7), home=2, away=1),
            _fixture(2, AT + timedelta(days=7), home=1, away=3),
        ]
    )

    lines = render_fixtures_context(fixtures, index, AT).splitlines()

    assert lines == [
        "Arsenal: previous v Chelsea (A), 2026-10-03T12:00Z; next v Fulham (H), 2026-10-17T12:00Z",
        "Chelsea: previous v Arsenal (H), 2026-10-03T12:00Z; next none",
        "Fulham: previous none; next v Arsenal (A), 2026-10-17T12:00Z",
    ]
