from datetime import UTC, datetime, timedelta

import pytest

from app.extraction.fixtures import FixtureIndex, FixtureRecord
from app.extraction.schemas import LinkedEvent
from app.extraction.store import (
    LINEUP_MAX_LEAD,
    ExtractionRecord,
    current_extractions,
    is_stale_lineup,
    save_extraction,
)
from app.fpl.models.reference import Fixture, Player, Season, Team
from app.tweets.models import Tweet
from tests.tweets.membership_helpers import set_members

SEASON = "2026/27"
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def _tweet(x_id: int, created_at: datetime, text: str = "Saka is out") -> Tweet:
    return Tweet(
        x_id=x_id,
        author_handle=f"author{x_id}",
        text=text,
        created_at=created_at,
        first_fetched_at=created_at,
        source="list",
        is_repost=False,
        is_reply=False,
        raw={},
    )


def _record(x_id: int, status: str = "extracted", finished: int = 0) -> ExtractionRecord:
    return ExtractionRecord(
        tweet_x_id=x_id,
        status=status,
        provider="fake",
        model="fake-model",
        prompt_version="extraction@1+link_disambiguation@1",
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=finished),
        attempts=1,
    )


def _event(
    event_type: str = "out",
    fpl_id: int | None = 7,
    mention: str = "Saka",
    certainty: str = "likely",
    for_next_pl_match: bool | None = None,
) -> LinkedEvent:
    return LinkedEvent(
        mention=mention,
        team=None,
        player_season=SEASON if fpl_id is not None else None,
        player_fpl_id=fpl_id,
        event_type=event_type,
        certainty=certainty,
        for_next_pl_match=for_next_pl_match,
    )


def _seed(session) -> None:
    session.add(Season(label=SEASON))
    session.add(Team(season=SEASON, fpl_id=1, name="Arsenal", short_name="ARS"))
    session.flush()
    session.add(
        Player(
            season=SEASON,
            fpl_id=7,
            web_name="Saka",
            first_name="Bukayo",
            second_name="Saka",
            team_fpl_id=1,
            position=3,
        )
    )
    session.commit()


def test_batch_by_ids_returns_the_latest_success_with_events_and_post_fields(db_session):
    _seed(db_session)
    db_session.add(_tweet(1, NOW, text="Saka is out for a month"))
    db_session.add(_tweet(2, NOW))
    db_session.commit()
    save_extraction(db_session, _record(1, finished=1), [_event("doubt")])
    latest = save_extraction(
        db_session, _record(1, finished=5), [_event("out"), _event(fpl_id=None)]
    )
    save_extraction(db_session, _record(2), [])

    rows = current_extractions(db_session, x_ids=[1])

    assert [r.tweet_x_id for r in rows] == [1]
    row = rows[0]
    assert row.extraction_id == latest
    assert row.created_at == NOW
    assert row.text == "Saka is out for a month"
    assert row.author_handle == "author1"
    assert [(e.event_type, e.player_fpl_id, e.player_web_name) for e in row.events] == [
        ("out", 7, "Saka"),
        ("out", None, None),
    ]


def test_a_failed_newer_extraction_does_not_hide_the_older_success(db_session):
    _seed(db_session)
    db_session.add(_tweet(1, NOW))
    db_session.commit()
    good = save_extraction(db_session, _record(1, finished=1), [_event()])
    save_extraction(db_session, _record(1, "failed", finished=9), [])

    rows = current_extractions(db_session, x_ids=[1])

    assert [r.extraction_id for r in rows] == [good]
    assert len(rows[0].events) == 1


def test_window_is_half_open_and_ordered_by_created_at_then_id(db_session):
    _seed(db_session)
    for x_id, minutes in [(3, 20), (1, 10), (2, 10), (4, 30), (5, 0)]:
        db_session.add(_tweet(x_id, NOW + timedelta(minutes=minutes)))
    db_session.commit()
    for x_id in range(1, 6):
        save_extraction(db_session, _record(x_id), [_event()])

    rows = current_extractions(
        db_session,
        created_from=NOW + timedelta(minutes=10),
        created_until=NOW + timedelta(minutes=30),
    )

    assert [r.tweet_x_id for r in rows] == [1, 2, 3]


def test_player_filter_keeps_posts_whose_current_extraction_names_the_player(db_session):
    _seed(db_session)
    db_session.add(
        Player(
            season=SEASON,
            fpl_id=8,
            web_name="Rice",
            first_name="Declan",
            second_name="Rice",
            team_fpl_id=1,
            position=3,
        )
    )
    for x_id in (1, 2, 3, 4):
        db_session.add(_tweet(x_id, NOW + timedelta(minutes=x_id)))
    db_session.commit()
    save_extraction(db_session, _record(1), [_event(fpl_id=7)])
    save_extraction(db_session, _record(2), [_event(fpl_id=8, mention="Rice")])
    # an older extraction named Saka, the current one does not
    save_extraction(db_session, _record(3, finished=1), [_event(fpl_id=7)])
    save_extraction(db_session, _record(3, finished=5), [_event(fpl_id=8, mention="Rice")])
    save_extraction(db_session, _record(4), [_event(fpl_id=8, mention="Rice"), _event(fpl_id=7)])

    rows = current_extractions(db_session, player=(SEASON, 7))

    assert [r.tweet_x_id for r in rows] == [1, 4]
    assert [e.player_fpl_id for e in rows[1].events] == [8, 7]


def test_the_window_does_not_let_an_older_extraction_through(db_session):
    _seed(db_session)
    db_session.add(_tweet(1, NOW + timedelta(minutes=10)))
    db_session.add(_tweet(2, NOW + timedelta(minutes=50)))
    db_session.commit()
    save_extraction(db_session, _record(1, finished=1), [_event("doubt")])
    newest = save_extraction(db_session, _record(1, finished=5), [_event("out")])
    save_extraction(db_session, _record(2), [_event()])

    rows = current_extractions(
        db_session, created_from=NOW, created_until=NOW + timedelta(minutes=30)
    )

    assert [(r.tweet_x_id, r.extraction_id) for r in rows] == [(1, newest)]
    assert [e.event_type for e in rows[0].events] == ["out"]


def test_posts_without_a_successful_extraction_are_absent(db_session):
    _seed(db_session)
    for x_id in (1, 2, 3):
        db_session.add(_tweet(x_id, NOW))
    db_session.commit()
    save_extraction(db_session, _record(1, "failed"), [])
    save_extraction(db_session, _record(2), [_event()])

    assert [r.tweet_x_id for r in current_extractions(db_session)] == [2]
    assert current_extractions(db_session, x_ids=[]) == []


def test_a_repost_exposes_its_original_author(db_session):
    _seed(db_session)
    repost = _tweet(1, NOW)
    repost.is_repost = True
    repost.reposted_author_handle = "origin"
    db_session.add(repost)
    db_session.add(_tweet(2, NOW))
    db_session.commit()
    save_extraction(db_session, _record(1), [_event()])
    save_extraction(db_session, _record(2), [_event()])

    rows = {r.tweet_x_id: r for r in current_extractions(db_session)}

    assert rows[1].is_repost and rows[1].reposted_author_handle == "origin"
    assert rows[2].reposted_author_handle is None


def test_sources_only_drops_context_posts(db_session):
    _seed(db_session)
    for x_id in (1, 2, 3, 4):
        db_session.add(_tweet(x_id, NOW))
    db_session.commit()
    db_session.get(Tweet, 4).quoted_x_id = 3
    db_session.get(Tweet, 4).author_handle = "member"
    db_session.get(Tweet, 1).author_handle = "member"
    db_session.commit()
    for x_id in (1, 2, 3):
        save_extraction(db_session, _record(x_id), [_event()])
    set_members(db_session.get_bind(), ["member"])

    everything = current_extractions(db_session)
    sources = current_extractions(db_session, sources_only=True)

    assert [r.tweet_x_id for r in everything] == [1, 2, 3]
    assert [r.tweet_x_id for r in sources] == [1, 3]


@pytest.mark.parametrize(
    ("event_type", "certainty", "lead", "stale"),
    [
        ("confirmed_starter", "confirmed", LINEUP_MAX_LEAD + timedelta(minutes=1), True),
        ("benched", "confirmed", LINEUP_MAX_LEAD + timedelta(minutes=1), True),
        ("confirmed_starter", "confirmed", LINEUP_MAX_LEAD, False),
        ("confirmed_starter", "confirmed", timedelta(minutes=75), False),
        ("confirmed_starter", "likely", timedelta(days=4), False),
        ("benched", "rumour", timedelta(days=4), False),
        ("out", "confirmed", timedelta(days=4), False),
        ("doubt", "confirmed", timedelta(days=4), False),
    ],
)
def test_a_confirmed_lineup_far_from_the_next_kickoff_is_stale(event_type, certainty, lead, stale):
    assert is_stale_lineup(event_type, certainty, NOW, NOW + lead) is stale


def test_a_lineup_with_no_known_next_kickoff_is_kept():
    assert not is_stale_lineup("confirmed_starter", "confirmed", NOW, None)


def _fixture(fpl_id: int, kickoff: datetime | None, home: int = 1, away: int = 2) -> Fixture:
    return Fixture(
        season=SEASON,
        fpl_id=fpl_id,
        kickoff_at=kickoff,
        team_h_fpl_id=home,
        team_a_fpl_id=away,
        finished=False,
    )


def test_fresh_lineups_only_drops_a_midweek_lineup_and_keeps_the_rest(db_session):
    _seed(db_session)
    db_session.add(Team(season=SEASON, fpl_id=2, name="Chelsea", short_name="CHE"))
    db_session.add(Team(season=SEASON, fpl_id=3, name="Fulham", short_name="FUL"))
    db_session.flush()
    saturday = NOW + timedelta(days=3)
    # the next kickoff is Arsenal's away match, the one after it must not count
    db_session.add(_fixture(1, saturday, home=3, away=1))
    db_session.add(_fixture(2, saturday + timedelta(days=7), home=1, away=2))
    # an earlier match of another club is not Arsenal's next one
    db_session.add(_fixture(3, NOW + timedelta(hours=2), home=2, away=3))
    db_session.add(_tweet(1, NOW, text="Saka starts against PSG"))
    db_session.add(_tweet(2, saturday - timedelta(minutes=75), text="Saka starts"))
    db_session.commit()
    save_extraction(
        db_session,
        _record(1),
        [
            _event("confirmed_starter", certainty="confirmed"),
            _event("confirmed_starter", certainty="likely"),
            _event("out", certainty="confirmed"),
        ],
    )
    save_extraction(db_session, _record(2), [_event("confirmed_starter", certainty="confirmed")])

    everything = {r.tweet_x_id: r for r in current_extractions(db_session)}
    fresh = {r.tweet_x_id: r for r in current_extractions(db_session, fresh_lineups_only=True)}

    assert len(everything[1].events) == 3
    assert [(e.event_type, e.certainty) for e in fresh[1].events] == [
        ("confirmed_starter", "likely"),
        ("out", "confirmed"),
    ]
    assert [(e.event_type, e.certainty) for e in fresh[2].events] == [
        ("confirmed_starter", "confirmed")
    ]


def test_fresh_lineups_only_keeps_a_lineup_when_the_club_has_no_fixture_ahead(db_session):
    _seed(db_session)
    db_session.add(Team(season=SEASON, fpl_id=2, name="Chelsea", short_name="CHE"))
    db_session.flush()
    db_session.add(_fixture(1, NOW - timedelta(days=1)))
    db_session.add(_fixture(2, None))
    db_session.add(_tweet(1, NOW))
    db_session.commit()
    save_extraction(db_session, _record(1), [_event("benched", certainty="confirmed")])

    [row] = current_extractions(db_session, fresh_lineups_only=True)

    assert [e.event_type for e in row.events] == ["benched"]


def test_a_post_left_with_no_fresh_event_keeps_its_row_without_events(db_session):
    _seed(db_session)
    db_session.add(Team(season=SEASON, fpl_id=2, name="Chelsea", short_name="CHE"))
    db_session.flush()
    db_session.add(_fixture(1, NOW + timedelta(days=4)))
    db_session.add(_tweet(1, NOW, text="Saka on the bench"))
    db_session.commit()
    save_extraction(db_session, _record(1), [_event("benched", certainty="confirmed")])

    [row] = current_extractions(db_session, fresh_lineups_only=True)

    assert row.events == []


def _seed_next_match(session, kickoff_in: timedelta = timedelta(hours=10)) -> None:
    _seed(session)
    session.add(Team(season=SEASON, fpl_id=2, name="Chelsea", short_name="CHE"))
    session.flush()
    session.add(_fixture(1, NOW + kickoff_in))
    session.add(_tweet(1, NOW, text="Saka starts"))
    session.commit()


def test_fresh_lineups_only_drops_a_lineup_the_check_answered_false(db_session):
    _seed_next_match(db_session)
    save_extraction(
        db_session,
        _record(1),
        [
            _event("benched", certainty="rumour", for_next_pl_match=False),
            _event("confirmed_starter", certainty="confirmed", for_next_pl_match=False),
            _event("out", certainty="confirmed", for_next_pl_match=False),
        ],
    )

    [row] = current_extractions(db_session, fresh_lineups_only=True)

    assert [(e.event_type, e.for_next_pl_match) for e in row.events] == [("out", False)]


def test_null_and_true_answers_meet_the_lead_filter(db_session):
    _seed_next_match(db_session, kickoff_in=timedelta(days=4))
    save_extraction(
        db_session,
        _record(1),
        [
            _event("benched", certainty="confirmed", for_next_pl_match=None),
            _event("benched", certainty="confirmed", for_next_pl_match=True),
            _event("benched", certainty="likely", for_next_pl_match=True),
            _event("confirmed_starter", certainty="likely", for_next_pl_match=None),
        ],
    )

    [row] = current_extractions(db_session, fresh_lineups_only=True)

    assert [(e.certainty, e.for_next_pl_match) for e in row.events] == [
        ("likely", True),
        ("likely", None),
    ]


def test_unfiltered_read_returns_every_event(db_session):
    _seed_next_match(db_session)
    save_extraction(
        db_session,
        _record(1),
        [_event("benched", for_next_pl_match=False), _event("benched", for_next_pl_match=None)],
    )

    [row] = current_extractions(db_session)

    assert [e.for_next_pl_match for e in row.events] == [False, None]


def test_a_kickoff_at_the_post_time_is_not_the_next_match(db_session):
    _seed(db_session)
    db_session.add(Team(season=SEASON, fpl_id=2, name="Chelsea", short_name="CHE"))
    db_session.flush()
    db_session.add(_fixture(1, NOW))
    db_session.add(_fixture(2, NOW + timedelta(days=4), home=2, away=1))
    db_session.add(_tweet(1, NOW, text="Saka on the bench"))
    db_session.commit()
    save_extraction(db_session, _record(1), [_event("benched", certainty="confirmed")])

    [row] = current_extractions(db_session, fresh_lineups_only=True)

    assert row.events == []
    fixtures = FixtureIndex(
        [
            FixtureRecord(SEASON, 1, None, NOW, 1, 2),
            FixtureRecord(SEASON, 2, None, NOW + timedelta(days=4), 2, 1),
        ]
    )
    assert fixtures.next_kickoff(1, NOW) == NOW + timedelta(days=4)
