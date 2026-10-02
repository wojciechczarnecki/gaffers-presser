from datetime import timedelta

from sqlmodel import Session, select

from app.alerts.breaking import run_breaking
from app.alerts.models import Alert, AlertPost
from app.alerts.render import load_template
from app.alerts.service import run_slot
from app.extraction.schemas import LinkedEvent
from app.extraction.store import ExtractionRecord, save_extraction
from tests.alerts.helpers import (
    GABRIEL_ID,
    alerts_runtime,
    scripted_corroboration,
    set_raw,
)
from tests.alerts.test_slots import DEADLINE_AT, REAL, seed
from tests.corroboration.helpers import ISAK, NOW, SAKA, SEASON, add_claim
from tests.delivery.fakes import FakeChannel, FixedClock
from tests.retrieval.helpers import add_tweet

TEMPLATE = load_template()
MARKER = TEMPLATE["link"]["new_marker"]
NEWS_AT = NOW + timedelta(minutes=90)


def minutes(value: float) -> timedelta:
    return timedelta(minutes=value)


def seconds_after_now(delta: timedelta) -> int:
    return int(delta.total_seconds())


def world(db, corroboration=None):
    seed(db)
    add_claim(db, 1, SAKA, "out", created_at=NOW - minutes(60), author="a1")
    clock = FixedClock(NOW)
    channel = FakeChannel(["msg-1"])
    runtime = alerts_runtime(db, channel, clock, corroboration)
    assert run_slot(db, runtime, REAL, 0, clock) == "sent"
    clock.advance(minutes(90))
    assert run_slot(db, runtime, REAL, 1, clock) == "skipped"
    channel.calls.clear()
    channel.keys.clear()
    return runtime, channel, clock


def add_late(db, x_id, player=SAKA, at=95, finished=97, author=None, event="out", **kwargs):
    add_claim(
        db,
        x_id,
        player,
        event,
        created_at=NOW + minutes(at),
        author=author or f"late{x_id}",
        finished=seconds_after_now(minutes(finished)),
        **kwargs,
    )


def alert_rows(db, key):
    with Session(db) as session:
        alert = session.exec(select(Alert).where(Alert.key == key)).one()
        posts = session.exec(select(AlertPost).where(AlertPost.alert_id == alert.id)).all()
        return alert, {(row.player_fpl_id, row.tweet_x_id): row.freshness for row in posts}


def breaking_keys(channel):
    return sorted(channel.keys)


def test_breaking_anchor_is_the_post_sql_only(db):
    corroboration, judge = scripted_corroboration()
    runtime, channel, clock = world(db, corroboration)
    # an older post that only now got extracted: it is the anchor although not the newest
    add_claim(
        db,
        2,
        SAKA,
        "out",
        created_at=NOW - minutes(120),
        author="older",
        finished=seconds_after_now(minutes(97)),
    )
    embedder_calls = len(corroboration.embedder.calls)
    clock.advance(minutes(8))

    assert run_breaking(db, runtime, REAL, NEWS_AT, clock) == 1

    assert channel.keys == ["alert:2026/27:gw6:breaking:2"]
    assert len(corroboration.embedder.calls) == embedder_calls
    assert judge.received_messages == []
    text = channel.calls[0].text
    anchor_line = next(line for line in text.splitlines() if "status/2" in line)
    anchor_prefix = TEMPLATE["link"]["anchor"].split("{account}")[0].format(marker=MARKER)
    assert anchor_line.strip().startswith(anchor_prefix)
    other = next(line for line in text.splitlines() if "status/1" in line)
    assert MARKER not in other
    alert, rows = alert_rows(db, "alert:2026/27:gw6:breaking:2")
    assert (alert.kind, alert.slot_minutes, alert.trigger_x_id, alert.status) == (
        "breaking",
        None,
        2,
        "sent",
    )
    assert rows == {(SAKA, 2): "new", (SAKA, 1): "context"}


def test_two_posts_extracted_in_one_tick_each_break(db):
    runtime, channel, clock = world(db)
    add_late(db, 2, at=95, finished=97)
    add_late(db, 3, at=96, finished=97)
    clock.advance(minutes(8))

    assert run_breaking(db, runtime, REAL, NEWS_AT, clock) == 2

    assert channel.keys == [
        "alert:2026/27:gw6:breaking:2",
        "alert:2026/27:gw6:breaking:3",
    ]
    assert "status/3" in channel.calls[0].text
    assert "status/2" in channel.calls[1].text
    first = alert_rows(db, "alert:2026/27:gw6:breaking:2")[1]
    second = alert_rows(db, "alert:2026/27:gw6:breaking:3")[1]
    assert first[(SAKA, 2)] == "new" and first[(SAKA, 3)] == "context"
    assert second[(SAKA, 3)] == "new" and second[(SAKA, 2)] == "context"


def test_included_post_or_its_repost_never_breaks(db):
    runtime, channel, clock = world(db)
    # a repost of x1, which the digest already included
    add_late(db, 5, author="rt1", is_repost=True, reposted_author_handle="a1")
    set_raw(db, 5, {"retweeted_tweet": {"id": 1}})
    clock.advance(minutes(8))

    assert run_breaking(db, runtime, REAL, NEWS_AT, clock) == 0
    assert channel.calls == []

    # a new post and its repost, extracted in the same tick, give one e-mail
    add_late(db, 6, author="origin6")
    add_late(db, 7, author="rt6", is_repost=True, reposted_author_handle="origin6")
    set_raw(db, 7, {"retweeted_tweet": {"id": 6}})

    assert run_breaking(db, runtime, REAL, NEWS_AT, clock) == 1
    assert channel.keys == ["alert:2026/27:gw6:breaking:6"]

    # a later repost of the post that already broke stays quiet
    add_late(db, 8, author="rt6b", is_repost=True, reposted_author_handle="origin6")
    set_raw(db, 8, {"retweeted_tweet": {"id": 6}})
    assert run_breaking(db, runtime, REAL, NEWS_AT, clock) == 0


def test_unlisted_player_does_not_break(db):
    runtime, channel, clock = world(db)
    add_late(db, 2, player=GABRIEL_ID, mention="Gabriel")
    clock.advance(minutes(8))

    assert run_breaking(db, runtime, REAL, NEWS_AT, clock) == 0
    assert channel.calls == []


def test_extracted_before_the_given_moment_is_not_breaking(db):
    runtime, channel, clock = world(db)
    since = NOW + minutes(99)
    add_late(db, 2, at=90, finished=98.9)
    add_late(db, 3, at=91, finished=99)
    clock.advance(minutes(8))

    assert run_breaking(db, runtime, REAL, since, clock) == 1
    assert channel.keys == ["alert:2026/27:gw6:breaking:3"]


def test_breaking_repeated_tick_sends_once(db):
    runtime, channel, clock = world(db)
    add_late(db, 2)
    clock.advance(minutes(8))

    assert run_breaking(db, runtime, REAL, NEWS_AT, clock) == 1
    assert run_breaking(db, runtime, REAL, NEWS_AT, clock) == 0

    assert len(channel.calls) == 1


def test_post_naming_two_listed_players_is_one_email_with_two_sections(db):
    runtime, channel, clock = world(db)
    add_tweet(db, 2, "Saka and Isak both out", created_at=NOW + minutes(95), author="late2")
    events = [
        LinkedEvent(
            mention=name,
            team=None,
            player_season=SEASON,
            player_fpl_id=fpl_id,
            event_type="out",
            certainty="likely",
        )
        for name, fpl_id in (("Saka", SAKA), ("Isak", ISAK))
    ]
    with Session(db) as session:
        save_extraction(
            session,
            ExtractionRecord(
                tweet_x_id=2,
                status="extracted",
                provider="fake",
                model="fake-model",
                prompt_version="x",
                started_at=NOW,
                finished_at=NOW + minutes(97),
                attempts=1,
            ),
            events,
        )
    clock.advance(minutes(8))

    assert run_breaking(db, runtime, REAL, NEWS_AT, clock) == 1

    assert channel.keys == ["alert:2026/27:gw6:breaking:2"]
    message = channel.calls[0]
    assert "Saka" in message.title and "Isak" in message.title
    assert message.text.count(TEMPLATE["section"]["links"]) == 2
    _, rows = alert_rows(db, "alert:2026/27:gw6:breaking:2")
    assert rows[(SAKA, 2)] == "new" and rows[(ISAK, 2)] == "new"


def test_no_breaking_at_or_after_deadline(db):
    runtime, channel, clock = world(db)
    add_late(db, 2)
    clock.advance(DEADLINE_AT - clock.now())

    assert run_breaking(db, runtime, REAL, NEWS_AT, clock) == 0
    assert channel.calls == []

    jumping = FixedClock(DEADLINE_AT - timedelta(seconds=5))
    original_now = jumping.now
    calls = []

    def now():
        calls.append(1)
        return original_now() if len(calls) <= 2 else DEADLINE_AT + timedelta(seconds=1)

    jumping.now = now
    runtime = alerts_runtime(db, channel, jumping)
    assert run_breaking(db, runtime, REAL, NEWS_AT, jumping) == 0
    assert channel.calls == []


def test_processed_until_skips_the_window_query_until_a_new_extraction(db, monkeypatch):
    from app.alerts import breaking

    runtime, channel, clock = world(db)
    add_late(db, 2, at=95, finished=97)
    clock.advance(minutes(8))
    assert run_breaking(db, runtime, REAL, NEWS_AT, clock) == 1
    with Session(db) as session:
        processed = breaking.newest_extraction(session)
    real = breaking.listed_players
    window_queries = []

    def counting(*args, **kwargs):
        window_queries.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(breaking, "listed_players", counting)

    assert run_breaking(db, runtime, REAL, NEWS_AT, clock, processed_until=processed) == 0
    assert window_queries == []
    # after a restart there is no mark: the last slot's as_of gates, nothing is sent twice
    assert run_breaking(db, runtime, REAL, NEWS_AT, clock) == 0
    assert window_queries == [1]

    add_late(db, 3, at=99, finished=101)
    clock.advance(minutes(5))
    assert run_breaking(db, runtime, REAL, NEWS_AT, clock, processed_until=processed) == 1
    assert window_queries == [1, 1]
    assert breaking_keys(channel) == [
        "alert:2026/27:gw6:breaking:2",
        "alert:2026/27:gw6:breaking:3",
    ]
