from datetime import timedelta
from decimal import Decimal

from sqlmodel import Session

from app.alerts.config import AlertConfig
from app.alerts.players import listed_players
from tests.alerts.helpers import SEASON, seed_league, set_ownership
from tests.corroboration.helpers import (
    GABRIEL_JESUS,
    GABRIEL_MAGALHAES,
    ISAK,
    NOW,
    SAKA,
    add_claim,
    seed_reference,
)

START = NOW - timedelta(days=7)
CONFIG = AlertConfig((120, 30), 3, Decimal("15"), None)


def listed(db, league_ids=(1,), config=CONFIG, start=START, as_of=NOW):
    with Session(db) as session:
        return listed_players(session, SEASON, list(league_ids), start, as_of, config)


def by_id(players):
    return {item.player.fpl_id: item for item in players}


def test_league_owned_uses_latest_synced_picks_with_managers(db):
    seed_reference(db, {4: NOW - timedelta(days=14), 5: NOW - timedelta(days=7)})
    seed_league(
        db,
        1,
        {10: ("Jan Kowalski", "Kowalski FC"), 11: ("Ewa Nowak", "Nowak XI")},
        {
            10: {
                4: [SAKA],
                5: {1: ISAK, 15: GABRIEL_JESUS, 16: GABRIEL_MAGALHAES},
            },
            11: {5: [ISAK]},
        },
    )
    seed_league(db, 2, {12: ("Outsider", "Other FC")}, {12: {5: [SAKA]}})
    for x_id, player in enumerate((SAKA, ISAK, GABRIEL_JESUS, GABRIEL_MAGALHAES), start=1):
        add_claim(db, x_id, player)

    players, without_claim = listed(db)

    found = by_id(players)
    assert set(found) == {ISAK, GABRIEL_JESUS}
    assert [(m.manager_name, m.team_name) for m in found[ISAK].managers] == [
        ("Ewa Nowak", "Nowak XI"),
        ("Jan Kowalski", "Kowalski FC"),
    ]
    assert [m.manager_name for m in found[GABRIEL_JESUS].managers] == ["Jan Kowalski"]
    assert found[ISAK].player.web_name == "Isak"
    assert found[ISAK].player.team_name == "Newcastle"
    assert without_claim == 0


def test_widely_owned_at_threshold(db):
    seed_reference(db)
    set_ownership(db, {SAKA: "14.9", ISAK: "15.0", GABRIEL_JESUS: None, GABRIEL_MAGALHAES: "80.0"})
    for x_id, player in enumerate((SAKA, ISAK, GABRIEL_JESUS), start=1):
        add_claim(db, x_id, player)

    players, without_claim = listed(db)

    assert set(by_id(players)) == {ISAK}
    assert by_id(players)[ISAK].widely_owned is True
    assert by_id(players)[ISAK].selected_by_percent == Decimal("15.0")
    assert without_claim == 1  # the 80% player has no claim


def test_trending_counts_independent_accounts_with_reposts(db):
    seed_reference(db)
    add_claim(db, 1, SAKA, author="alpha")
    add_claim(db, 2, SAKA, author="beta")
    add_claim(db, 3, SAKA, author="gamma", is_repost=True, reposted_author_handle="Alpha")
    add_claim(db, 4, SAKA, author="epsilon", created_at=START - timedelta(hours=1))

    players, _ = listed(db)
    assert players == []

    add_claim(db, 5, SAKA, author="delta")
    players, _ = listed(db)

    assert [(p.player.fpl_id, p.trending_accounts) for p in players] == [(SAKA, 3)]
    assert players[0].managers == () and players[0].widely_owned is False


def test_only_players_with_a_claim_are_reported_once_with_all_categories(db):
    seed_reference(db, {5: NOW - timedelta(days=7)})
    seed_league(
        db,
        1,
        {10: ("Jan Kowalski", "Kowalski FC")},
        {10: {5: [SAKA, ISAK]}},
    )
    set_ownership(db, {SAKA: "40.0", GABRIEL_JESUS: "50.0"})
    for x_id, author in enumerate(("a", "b", "c"), start=1):
        add_claim(db, x_id, SAKA, author=author)
    add_claim(db, 10, GABRIEL_MAGALHAES, author="z")  # claimed but not listed

    players, without_claim = listed(db)

    assert [p.player.fpl_id for p in players] == [SAKA]
    only = players[0]
    assert [m.manager_name for m in only.managers] == ["Jan Kowalski"]
    assert only.widely_owned is True
    assert only.trending_accounts == 3
    assert only.claim_x_ids == (1, 2, 3)
    assert without_claim == 2  # ISAK (league-owned) and JESUS (widely owned) have no claim


def test_order_by_ownership_nulls_last(db):
    seed_reference(db, {5: NOW - timedelta(days=7)})
    seed_league(
        db,
        1,
        {10: ("Jan Kowalski", "Kowalski FC")},
        {10: {5: [SAKA, ISAK, GABRIEL_JESUS, GABRIEL_MAGALHAES]}},
    )
    set_ownership(db, {SAKA: "20.5", ISAK: "40.0"})
    for x_id, player in enumerate((SAKA, ISAK, GABRIEL_JESUS, GABRIEL_MAGALHAES), start=1):
        add_claim(db, x_id, player)

    players, _ = listed(db)

    assert [p.player.web_name for p in players] == ["Isak", "Saka", "Gabriel", "Jesus"]
