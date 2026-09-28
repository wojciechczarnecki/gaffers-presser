from datetime import UTC, datetime, timedelta

from app.extraction.schemas import LinkedEvent
from app.extraction.store import (
    ExtractionRecord,
    current_extraction,
    extraction_status,
    next_pending,
    posts_for_reextract,
    save_extraction,
)
from app.fpl.models.reference import Player, Season, Team
from app.tweets.models import Tweet

SEASON = "2026/27"
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def _tweet(
    x_id: int, created_at: datetime, is_repost: bool = False, is_reply: bool = False
) -> Tweet:
    return Tweet(
        x_id=x_id,
        author_handle="reporter",
        text="Haaland starts today.",
        created_at=created_at,
        first_fetched_at=created_at,
        source="list",
        is_repost=is_repost,
        is_reply=is_reply,
        raw={},
    )


def _seed_player(session, fpl_id: int) -> None:
    session.add(Season(label=SEASON))
    session.add(Team(season=SEASON, fpl_id=1, name="Team One", short_name="ONE"))
    session.flush()
    session.add(
        Player(
            season=SEASON,
            fpl_id=fpl_id,
            web_name="Haaland",
            first_name="Erling",
            second_name="Haaland",
            team_fpl_id=1,
            position=1,
        )
    )
    session.commit()


def _record(x_id: int, status: str = "extracted", **overrides) -> ExtractionRecord:
    fields = dict(
        tweet_x_id=x_id,
        status=status,
        provider="fake",
        model="fake-model",
        prompt_version="extraction@1+link_disambiguation@1",
        started_at=NOW,
        finished_at=NOW + timedelta(seconds=2),
        attempts=1,
    )
    fields.update(overrides)
    return ExtractionRecord(**fields)


def test_save_extraction_roundtrip(db_session):
    _seed_player(db_session, fpl_id=10)
    db_session.add(_tweet(1, NOW))
    db_session.commit()

    linked = LinkedEvent(
        mention="Haaland",
        team=None,
        player_season=SEASON,
        player_fpl_id=10,
        event_type="confirmed_starter",
        certainty="confirmed",
    )
    unlinked = LinkedEvent(
        mention="Some Nobody",
        team="Team One",
        player_season=None,
        player_fpl_id=None,
        event_type="doubt",
        certainty="rumour",
    )
    record = _record(1, input_tokens=10, output_tokens=5, cost_usd=0.001, latency_seconds=1.5)

    extraction_id = save_extraction(db_session, record, [linked, unlinked])

    current = current_extraction(db_session, 1)
    assert current is not None
    assert current.extraction_id == extraction_id
    assert current.provider == "fake"
    assert current.model == "fake-model"
    assert current.prompt_version == "extraction@1+link_disambiguation@1"
    assert current.started_at == NOW
    by_mention = {e.mention: e for e in current.events}
    assert by_mention["Haaland"].player_fpl_id == 10
    assert by_mention["Haaland"].player_season == SEASON
    assert by_mention["Some Nobody"].player_fpl_id is None
    assert by_mention["Some Nobody"].team_mention == "Team One"


def test_reextraction_keeps_history_current_is_latest_extracted(db_session):
    db_session.add(_tweet(1, NOW))
    db_session.commit()

    save_extraction(db_session, _record(1, "extracted", finished_at=NOW), [])
    save_extraction(db_session, _record(1, "failed", finished_at=NOW + timedelta(seconds=10)), [])
    third_id = save_extraction(
        db_session, _record(1, "extracted", finished_at=NOW + timedelta(seconds=20)), []
    )

    current = current_extraction(db_session, 1)
    assert current is not None
    assert current.extraction_id == third_id


def test_current_extraction_exposes_post_flags(db_session):
    db_session.add(_tweet(1, NOW, is_repost=True, is_reply=False))
    db_session.commit()
    save_extraction(db_session, _record(1), [])

    current = current_extraction(db_session, 1)

    assert current is not None
    assert current.is_repost is True
    assert current.is_reply is False
    assert current.author_handle == "reporter"


def test_next_pending_oldest_first(db_session):
    db_session.add(_tweet(2, NOW + timedelta(seconds=10)))
    db_session.add(_tweet(1, NOW))
    db_session.commit()
    save_extraction(db_session, _record(1), [])

    post = next_pending(db_session)

    assert post is not None
    assert post.x_id == 2


def test_next_pending_none_when_all_extracted(db_session):
    db_session.add(_tweet(1, NOW))
    db_session.commit()
    save_extraction(db_session, _record(1), [])

    assert next_pending(db_session) is None


def test_posts_for_reextract_selectors(db_session):
    db_session.add(_tweet(1, NOW))
    db_session.add(_tweet(2, NOW + timedelta(hours=1)))
    db_session.add(_tweet(3, NOW + timedelta(hours=2)))
    db_session.commit()
    save_extraction(db_session, _record(2, "failed"), [])
    save_extraction(db_session, _record(3, "extracted"), [])

    by_id = posts_for_reextract(db_session, x_id=1)
    assert [p.x_id for p in by_id] == [1]

    by_range = posts_for_reextract(
        db_session, since=NOW + timedelta(minutes=30), until=NOW + timedelta(hours=1, minutes=30)
    )
    assert [p.x_id for p in by_range] == [2]

    failed = posts_for_reextract(db_session, failed=True)
    assert [p.x_id for p in failed] == [2]


def test_extraction_status_counts(db_session):
    db_session.add(_tweet(1, NOW))
    db_session.add(_tweet(2, NOW + timedelta(hours=1)))
    db_session.commit()
    save_extraction(db_session, _record(1, "failed", finished_at=NOW), [])
    save_extraction(
        db_session,
        _record(2, "extracted", finished_at=NOW + timedelta(hours=1), latency_seconds=3.0),
        [],
    )
    db_session.add(_tweet(3, NOW + timedelta(hours=2)))
    db_session.commit()

    status = extraction_status(db_session.get_bind())

    assert status.waiting == 1
    assert status.failed_posts == 1
    assert status.latest is not None
    assert status.latest.tweet_x_id == 2
    assert status.latest.status == "extracted"
    assert status.latest.latency_seconds == 3.0
