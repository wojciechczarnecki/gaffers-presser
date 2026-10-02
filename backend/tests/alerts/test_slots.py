from datetime import timedelta

from sqlmodel import Session, select

from app.alerts.models import Alert, AlertPost
from app.alerts.render import load_template
from app.alerts.schemas import AlertDeadline
from app.alerts.service import run_slot
from app.delivery.channels.base import ChannelRejectedError
from app.delivery.models import DeliveryLog
from tests.alerts.helpers import (
    SEASON,
    alerts_runtime,
    embed_tweet,
    scripted_corroboration,
    seed_league,
)
from tests.corroboration.helpers import ISAK, NOW, SAKA, add_claim, seed_reference
from tests.delivery.fakes import FakeChannel, FixedClock
from tests.retrieval.helpers import add_tweet

TEMPLATE = load_template()
PREVIOUS = NOW - timedelta(days=3)
DEADLINE_AT = NOW + timedelta(hours=2)  # the digest is due at NOW, the news at NOW + 90 min
REAL = AlertDeadline(f"{SEASON}:gw6", DEADLINE_AT, False, SEASON, 6)
REHEARSAL = AlertDeadline("rehearsal:2026-09-29T20:00Z", DEADLINE_AT, True, None, None)
DIGEST_KEY = "alert:2026/27:gw6:digest:120"
NEWS_KEY = "alert:2026/27:gw6:news:30"
MARKER = TEMPLATE["link"]["new_marker"]


def seed(db):
    seed_reference(db, {5: PREVIOUS, 6: DEADLINE_AT})
    seed_league(
        db,
        1,
        {10: ("Jan Kowalski", "Kowalski FC")},
        {10: {5: [SAKA, ISAK]}},
    )


def setup(db, corroboration=None, channel=None, at=NOW):
    clock = FixedClock(at)
    channel = channel or FakeChannel(["msg-1"])
    runtime = alerts_runtime(db, channel, clock, corroboration)
    return runtime, channel, clock


def alerts(db):
    with Session(db) as session:
        return list(session.exec(select(Alert).order_by(Alert.id)).all())


def alert_posts(db, key):
    with Session(db) as session:
        alert = session.exec(select(Alert).where(Alert.key == key)).one()
        rows = session.exec(select(AlertPost).where(AlertPost.alert_id == alert.id)).all()
        return {(row.player_fpl_id, row.tweet_x_id): row.freshness for row in rows}


def test_digest_covers_previous_deadline_to_slot_with_full_corroboration(db):
    seed(db)
    add_claim(db, 1, SAKA, "out", created_at=PREVIOUS - timedelta(hours=1), author="before")
    add_claim(db, 2, SAKA, "out", created_at=NOW - timedelta(hours=5), author="a2")
    add_claim(db, 3, SAKA, "out", created_at=NOW - timedelta(hours=2), author="a3")
    add_claim(db, 4, SAKA, "out", created_at=NOW + timedelta(minutes=5), author="after")
    add_tweet(db, 10, "Saka is out for weeks", created_at=NOW - timedelta(hours=1), author="j1")
    corroboration, fake = scripted_corroboration("supports")
    runtime, channel, clock = setup(db, corroboration)

    assert run_slot(db, runtime, REAL, 0, clock) == "sent"

    assert channel.keys == [DIGEST_KEY]
    assert len(fake.received_messages) == 1
    (row,) = alerts(db)
    assert (row.key, row.kind, row.slot_minutes, row.status, row.as_of) == (
        DIGEST_KEY,
        "digest",
        120,
        "sent",
        NOW,
    )
    assert row.deadline_key == REAL.key
    with Session(db) as session:
        delivery = session.exec(select(DeliveryLog)).one()
    assert row.delivery_log_id == delivery.id and delivery.status == "sent"
    assert alert_posts(db, DIGEST_KEY) == {
        (SAKA, 2): "new",
        (SAKA, 3): "new",
        (SAKA, 10): "new",
    }
    assert "Saka" in channel.calls[0].text
    assert "https://x.com/j1/status/10" in channel.calls[0].text
    assert "status/1\n" not in channel.calls[0].text


def test_digest_without_claims_says_no_news(db):
    seed(db)
    runtime, channel, clock = setup(db)

    assert run_slot(db, runtime, REAL, 0, clock) == "sent"

    assert len(channel.calls) == 1
    text = channel.calls[0].text
    assert TEMPLATE["digest"]["empty"] in text
    assert TEMPLATE["digest"]["no_news"].format(count=2) in text
    assert alert_posts(db, DIGEST_KEY) == {}


def test_news_reports_only_players_with_unincluded_posts_and_marks_new(db):
    seed(db)
    add_claim(db, 1, SAKA, "out", created_at=NOW - timedelta(hours=3), author="a1")
    add_claim(
        db, 2, ISAK, "doubt", created_at=NOW - timedelta(hours=3), author="a2", mention="Isak"
    )
    runtime, channel, clock = setup(db)
    run_slot(db, runtime, REAL, 0, clock)
    add_claim(db, 3, SAKA, "out", created_at=NOW + timedelta(minutes=20), author="a3")
    clock.advance(timedelta(minutes=90))

    assert run_slot(db, runtime, REAL, 1, clock) == "sent"

    assert channel.keys == [DIGEST_KEY, NEWS_KEY]
    news = channel.calls[1].text
    assert "Saka" in news and "Isak" not in news
    marked = [line for line in news.splitlines() if MARKER in line]
    assert len(marked) == 1 and "status/3" in marked[0]
    assert alert_posts(db, NEWS_KEY) == {(SAKA, 1): "context", (SAKA, 3): "new"}


def test_news_with_nothing_new_is_skipped(db):
    seed(db)
    add_claim(db, 1, SAKA, "out", created_at=NOW - timedelta(hours=3), author="a1")
    runtime, channel, clock = setup(db)
    run_slot(db, runtime, REAL, 0, clock)
    clock.advance(timedelta(minutes=90))

    assert run_slot(db, runtime, REAL, 1, clock) == "skipped"

    assert len(channel.calls) == 1
    news = alerts(db)[1]
    assert (news.key, news.status, news.delivery_log_id) == (NEWS_KEY, "skipped", None)
    assert alert_posts(db, NEWS_KEY) == {}


def test_repeated_run_sends_once(db):
    seed(db)
    add_claim(db, 1, SAKA, "out", created_at=NOW - timedelta(hours=3), author="a1")
    runtime, channel, clock = setup(db)

    assert run_slot(db, runtime, REAL, 0, clock) == "sent"
    assert run_slot(db, runtime, REAL, 0, clock) == "exists"

    assert len(channel.calls) == 1 and len(alerts(db)) == 1


def test_lost_record_after_send_is_recovered_without_resend(db):
    seed(db)
    add_claim(db, 1, SAKA, "out", created_at=NOW - timedelta(hours=3), author="a1")
    runtime, channel, clock = setup(db)
    run_slot(db, runtime, REAL, 0, clock)
    (first,) = alerts(db)
    with Session(db) as session:
        session.exec(AlertPost.__table__.delete())
        session.exec(Alert.__table__.delete())
        session.commit()

    assert run_slot(db, runtime, REAL, 0, clock) == "sent"

    assert len(channel.calls) == 1
    (again,) = alerts(db)
    assert again.status == "sent" and again.delivery_log_id == first.delivery_log_id


def test_restart_sends_missed_slot_up_to_send_time(db):
    seed(db)
    add_claim(db, 1, SAKA, "out", created_at=NOW - timedelta(hours=3), author="a1")
    add_claim(db, 2, SAKA, "out", created_at=NOW + timedelta(minutes=30), author="a2")
    add_claim(db, 3, SAKA, "out", created_at=NOW + timedelta(minutes=99), author="a3")
    add_claim(db, 4, SAKA, "out", created_at=NOW + timedelta(minutes=105), author="a4")
    runtime, channel, clock = setup(db, at=DEADLINE_AT - timedelta(minutes=20))

    assert run_slot(db, runtime, REAL, 0, clock) == "sent"

    (row,) = alerts(db)
    assert row.as_of == DEADLINE_AT - timedelta(minutes=20)
    assert alert_posts(db, DIGEST_KEY) == {(SAKA, 1): "new", (SAKA, 2): "new", (SAKA, 3): "new"}


class JumpingClock(FixedClock):
    def __init__(self, first, later):
        super().__init__(first)
        self.later = later
        self.calls = 0

    def now(self):
        self.calls += 1
        return self._now if self.calls == 1 else self.later


def test_no_send_at_or_after_deadline(db):
    seed(db)
    add_claim(db, 1, SAKA, "out", created_at=NOW - timedelta(hours=3), author="a1")
    channel = FakeChannel(["msg-1"])
    clock = JumpingClock(DEADLINE_AT - timedelta(seconds=30), DEADLINE_AT + timedelta(seconds=1))
    runtime = alerts_runtime(db, channel, clock)

    assert run_slot(db, runtime, REAL, 0, clock) == "cutoff"

    assert channel.calls == [] and alerts(db) == []

    at_deadline = FixedClock(DEADLINE_AT)
    assert run_slot(db, alerts_runtime(db, channel, at_deadline), REAL, 0, at_deadline) == "cutoff"
    assert channel.calls == [] and alerts(db) == []


def test_failed_delivery_recorded_and_not_retried(db):
    seed(db)
    add_claim(db, 1, SAKA, "out", created_at=NOW - timedelta(hours=3), author="a1")
    channel = FakeChannel([ChannelRejectedError("rejected", 422)])
    runtime, channel, clock = setup(db, channel=channel)

    assert run_slot(db, runtime, REAL, 0, clock) == "failed"
    assert run_slot(db, runtime, REAL, 0, clock) == "exists"
    clock.advance(timedelta(minutes=90))
    assert run_slot(db, runtime, REAL, 1, clock) == "skipped"

    assert len(channel.calls) == 1
    first, news = alerts(db)
    assert (first.status, news.status) == ("failed", "skipped")
    with Session(db) as session:
        assert session.exec(select(DeliveryLog)).one().status == "failed"
    assert alert_posts(db, DIGEST_KEY) == {(SAKA, 1): "new"}


def test_failed_corroboration_falls_back_to_sql_with_note(db, monkeypatch):
    seed(db)
    add_claim(db, 1, SAKA, "out", created_at=NOW - timedelta(hours=3), author="a1")
    add_claim(
        db, 2, ISAK, "doubt", created_at=NOW - timedelta(hours=3), author="a2", mention="Isak"
    )
    add_tweet(db, 10, "Saka and Isak news", created_at=NOW - timedelta(hours=1), author="j1")
    embed_tweet(db, 10)
    corroboration, _ = scripted_corroboration("supports", "supports")
    runtime, channel, clock = setup(db, corroboration)
    from app.alerts import service

    real = service.corroborate

    def flaky(engine, player, as_of, new_since=None, **kwargs):
        if kwargs["runtime"].embedder is not None and player.fpl_id == ISAK:
            raise RuntimeError("search exploded")
        return real(engine, player, as_of, new_since, **kwargs)

    monkeypatch.setattr(service, "corroborate", flaky)

    assert run_slot(db, runtime, REAL, 0, clock) == "sent"

    text = channel.calls[0].text
    assert "Isak" in text and "Saka" in text
    assert text.count(TEMPLATE["note"]["search_failed"]) == 1
    isak_section = text[text.index("Isak") :]
    assert TEMPLATE["note"]["search_failed"] in isak_section
    assert TEMPLATE["note"]["search_failed"] not in text[: text.index("Isak")]


def test_rehearsal_alerts_do_not_count_for_the_real_deadline(db):
    seed(db)
    add_claim(db, 1, SAKA, "out", created_at=NOW - timedelta(hours=3), author="a1")
    runtime, channel, clock = setup(db)
    rehearsal_news_key = "alert:rehearsal:2026-09-29T20:00Z:news:30"

    assert run_slot(db, runtime, REHEARSAL, 0, clock) == "sent"
    clock.advance(timedelta(minutes=90))
    assert run_slot(db, runtime, REHEARSAL, 1, clock) == "skipped"
    clock.advance(-timedelta(minutes=90))

    assert run_slot(db, runtime, REAL, 0, clock) == "sent"
    clock.advance(timedelta(minutes=90))
    assert run_slot(db, runtime, REAL, 1, clock) == "skipped"

    assert channel.keys == [
        "alert:rehearsal:2026-09-29T20:00Z:digest:120",
        DIGEST_KEY,
    ]
    assert alert_posts(db, DIGEST_KEY) == {(SAKA, 1): "new"}
    assert alert_posts(db, "alert:rehearsal:2026-09-29T20:00Z:digest:120") == {(SAKA, 1): "new"}
    assert any(line for line in channel.calls[1].text.splitlines() if MARKER in line)
    assert rehearsal_news_key in {a.key for a in alerts(db)}
