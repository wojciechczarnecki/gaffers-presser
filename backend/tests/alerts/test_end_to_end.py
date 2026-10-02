import threading
from datetime import datetime, timedelta
from decimal import Decimal

from sqlmodel import Session, select

from app.alerts.config import AlertConfig
from app.alerts.loop import AlertLoop
from app.alerts.models import Alert, AlertPost
from app.alerts.schedule import rehearsal_key
from app.alerts.store import included_origins
from tests.alerts.helpers import alerts_runtime, seed_league, set_ownership, set_raw
from tests.corroboration.helpers import ISAK, NOW, SAKA, add_claim, seed_reference
from tests.delivery.fakes import FakeChannel, FixedClock

DEADLINE = NOW + timedelta(hours=2)
REHEARSAL = DEADLINE + timedelta(hours=5)
REAL_KEY = "2026/27:gw6"
REHEARSAL_KEY = rehearsal_key(REHEARSAL)


def at(moment: datetime) -> int:
    return int((moment - NOW).total_seconds())


def claim(db, x_id, player, created, finished, author, **kwargs):
    add_claim(
        db,
        x_id,
        player,
        "out" if player == SAKA else "doubt",
        created_at=created,
        author=author,
        finished=at(finished),
        mention="Saka" if player == SAKA else "Isak",
        **kwargs,
    )


def minutes(value: float) -> timedelta:
    return timedelta(minutes=value)


def rows(db, key_prefix):
    with Session(db) as session:
        alerts = session.exec(select(Alert).order_by(Alert.id)).all()
        return [a for a in alerts if a.key.startswith(key_prefix)]


def posts(db, key):
    with Session(db) as session:
        alert = session.exec(select(Alert).where(Alert.key == key)).one()
        found = session.exec(select(AlertPost).where(AlertPost.alert_id == alert.id)).all()
        return {(p.player_fpl_id, p.tweet_x_id): p.freshness for p in found}


def test_one_simulated_deadline_and_one_rehearsal(db):
    seed_reference(db, {5: NOW - timedelta(days=7), 6: DEADLINE})
    seed_league(db, 1, {10: ("Jan Kowalski", "Kowalski FC")}, {10: {5: [SAKA]}})
    set_ownership(db, {SAKA: "40.0", ISAK: "20.0"})
    clock = FixedClock(NOW)
    channel = FakeChannel(["msg-1"])
    # the rehearsal comes after the real deadline in this simulation
    runtime = alerts_runtime(
        db, channel, clock, config=AlertConfig((120, 30), 3, Decimal("15"), REHEARSAL)
    )
    loop = AlertLoop(db, runtime, clock, threading.Event())

    # T-120: the digest covers everything since the previous deadline
    claim(db, 1, SAKA, NOW - minutes(120), NOW - minutes(119), "a1")
    claim(db, 2, ISAK, NOW - minutes(180), NOW - minutes(179), "a2")
    assert loop.tick() == 60.0
    assert channel.keys == [f"alert:{REAL_KEY}:digest:120"]
    assert posts(db, channel.keys[0]) == {(SAKA, 1): "new", (ISAK, 2): "new"}

    # a post between the slots changes nothing until T-30
    claim(db, 3, SAKA, NOW + minutes(20), NOW + minutes(21), "a3")
    clock.advance(minutes(30))
    loop.tick()
    assert len(channel.keys) == 1

    # T-30: the news reports only the player with a new post and marks it
    clock.advance(minutes(60))
    assert loop.tick() == 5.0
    assert channel.keys[-1] == f"alert:{REAL_KEY}:news:30"
    assert posts(db, channel.keys[-1]) == {(SAKA, 1): "context", (SAKA, 3): "new"}
    assert "Isak" not in channel.calls[-1].text

    # after T-30 every new post breaks once; a repost of an included post does not
    claim(db, 4, SAKA, NOW + minutes(93), NOW + minutes(94), "a4")
    claim(db, 5, ISAK, NOW + minutes(93.5), NOW + minutes(94), "a5")
    clock.advance(minutes(5))
    assert loop.tick() == 5.0
    assert channel.keys[-2:] == [
        f"alert:{REAL_KEY}:breaking:4",
        f"alert:{REAL_KEY}:breaking:5",
    ]
    claim(
        db,
        6,
        SAKA,
        NOW + minutes(98),
        NOW + minutes(99),
        "rt4",
        is_repost=True,
        reposted_author_handle="a4",
    )
    set_raw(db, 6, {"retweeted_tweet": {"id": 4}})
    claim(
        db,
        7,
        SAKA,
        NOW + minutes(98.5),
        NOW + minutes(99),
        "rt1",
        is_repost=True,
        reposted_author_handle="a1",
    )
    set_raw(db, 7, {"retweeted_tweet": {"id": 1}})
    clock.advance(minutes(5))
    assert loop.tick() == 5.0
    assert len(channel.keys) == 4
    assert loop.tick() == 5.0  # a repeated tick sends nothing
    assert len(channel.keys) == 4

    # at the deadline nothing more goes out, even with a fresh post waiting
    claim(db, 8, SAKA, NOW + minutes(118), NOW + minutes(119), "a8")
    clock.advance(DEADLINE - clock.now())
    loop.tick()
    assert len(channel.keys) == 4
    real_alerts = rows(db, "alert:" + REAL_KEY)
    assert [(a.kind, a.status) for a in real_alerts] == [
        ("digest", "sent"),
        ("news", "sent"),
        ("breaking", "sent"),
        ("breaking", "sent"),
    ]
    with Session(db) as session:
        real_included = included_origins(session, REAL_KEY)
    assert {1, 3, 4, 5} <= real_included and 8 not in real_included

    # the rehearsal: its own digest, news and breaking for its own key
    claim(db, 10, SAKA, DEADLINE + minutes(30), DEADLINE + minutes(31), "b1")
    claim(db, 11, ISAK, DEADLINE + minutes(40), DEADLINE + minutes(41), "b2")
    clock.advance(REHEARSAL - minutes(120) - clock.now())
    assert loop.tick() == 60.0
    digest = f"alert:{REHEARSAL_KEY}:digest:120"
    assert channel.keys[-1] == digest
    # the rehearsal window starts at the previous real deadline, so post 8 is out of it
    assert posts(db, digest) == {(SAKA, 10): "new", (ISAK, 11): "new"}

    claim(db, 12, SAKA, REHEARSAL - minutes(60), REHEARSAL - minutes(59), "b3")
    clock.advance(minutes(90))
    assert loop.tick() == 5.0
    assert channel.keys[-1] == f"alert:{REHEARSAL_KEY}:news:30"
    assert posts(db, channel.keys[-1])[(SAKA, 12)] == "new"

    claim(db, 13, ISAK, REHEARSAL - minutes(20), REHEARSAL - minutes(19), "b4")
    clock.advance(minutes(11))
    assert loop.tick() == 5.0
    assert channel.keys[-1] == f"alert:{REHEARSAL_KEY}:breaking:13"

    clock.advance(REHEARSAL - clock.now())
    loop.tick()
    assert channel.keys[-1] == f"alert:{REHEARSAL_KEY}:breaking:13"
    assert len(channel.keys) == 7

    with Session(db) as session:
        assert included_origins(session, REAL_KEY) == real_included
        assert included_origins(session, REHEARSAL_KEY) >= {10, 11, 12, 13}
    assert len(rows(db, "alert:" + REAL_KEY)) == 4
    assert len(set(channel.keys)) == len(channel.keys)
