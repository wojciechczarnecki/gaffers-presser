from datetime import UTC, datetime, timedelta

from app.extraction.schemas import LinkedEvent
from app.extraction.store import ExtractionRecord, current_extractions, save_extraction
from app.fpl.models.reference import Player, Season, Team
from app.tweets.models import Tweet

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


def _event(event_type: str = "out", fpl_id: int | None = 7, mention: str = "Saka") -> LinkedEvent:
    return LinkedEvent(
        mention=mention,
        team=None,
        player_season=SEASON if fpl_id is not None else None,
        player_fpl_id=fpl_id,
        event_type=event_type,
        certainty="likely",
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


def test_posts_without_a_successful_extraction_are_absent(db_session):
    _seed(db_session)
    for x_id in (1, 2, 3):
        db_session.add(_tweet(x_id, NOW))
    db_session.commit()
    save_extraction(db_session, _record(1, "failed"), [])
    save_extraction(db_session, _record(2), [_event()])

    assert [r.tweet_x_id for r in current_extractions(db_session)] == [2]
    assert current_extractions(db_session, x_ids=[]) == []
