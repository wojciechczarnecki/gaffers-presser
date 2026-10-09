import json

import pytest

from app.presser.evaluation import building
from app.presser.evaluation.building import (
    PseudonymisationError,
    Pseudonyms,
    build_real_cases,
)
from app.presser.evaluation.cases import HistoryEntry
from tests.presser.helpers import SEASON, World

POOL = tuple(f"Pseudo{n}" for n in range(30))
LEAGUES = ("Liga Pierwsza", "Liga Druga")
PSEUDONYMS = Pseudonyms(POOL, LEAGUES)
LEAGUE_1, LEAGUE_2 = 987654001, 987654002
ENTRIES = {LEAGUE_1: [880000011, 880000012, 880000013], LEAGUE_2: [880000021, 880000022]}


@pytest.fixture
def world(db):
    world = World(db, gameweeks=3, league_id=LEAGUE_1, league_name="Prawdziwa Liga")
    world.add_league(LEAGUE_2, "Druga Realna")
    world.player(1, "Haaland")
    world.player(2, "Salah")
    names = [
        "Zenobiusz Realny",
        "Aurelia Prawdziwa",
        "Bonifacy Kowalski",
        "Cyprian Nowak",
        "Dorota Mazur",
    ]
    flat = [entry for entries in ENTRIES.values() for entry in entries]
    for league_id, entries in ENTRIES.items():
        for entry in entries:
            name = names[flat.index(entry)]
            team = "Haaland Army" if entry == flat[0] else f"Realni FC {entry}"
            world.manager(entry, name, team_name=team, league_id=league_id)
            for gameweek in (1, 2, 3):
                world.gw(entry, gameweek, 40 + entry % 17 + gameweek, captain=1, vice=2)
    for gameweek in (1, 2, 3):
        world.result(1, gameweek, 90, 6 + gameweek)
        world.result(2, gameweek, 90, 3)
    return world


def build(db, history=()):
    return build_real_cases(db, SEASON, [1, 2, 3], PSEUDONYMS, list(history), seed=1)


def test_build_pseudonymises(db, world):
    built = build(db)
    blob = "\n".join(case.model_dump_json() + case.id for case in built.cases)
    for real in ("Zenobiusz", "Realny", "Realni", "Prawdziwa", "Druga Realna", "Bonifacy", "Mazur"):
        assert real not in blob
    assert len(built.cases) == 6
    pool = set(POOL)
    for case in built.cases:
        facts = case.facts
        assert facts.league in LEAGUES
        assert {score.manager for score in facts.winners + facts.flops} <= pool
        assert {row.manager for row in facts.table.rows} <= pool
        assert {pick.manager for pick in facts.captaincy.picks} <= pool
        assert str(LEAGUE_1) not in case.id and str(LEAGUE_2) not in case.id
        assert "88000" not in json.dumps(case.model_dump(mode="json")) + case.id
    assert [case.id for case in built.cases][:3] == ["real-l1-gw1", "real-l1-gw2", "real-l1-gw3"]
    assert {case.split for case in built.cases} == {"dev", "test"}
    assert [case.tags for case in built.cases if case.facts.gameweek == 1] == [
        ["first_gameweek"],
        ["first_gameweek"],
    ]


def test_pseudonyms_are_deterministic(db, world):
    assert [c.model_dump_json() for c in build(db).cases] == [
        c.model_dump_json() for c in build(db).cases
    ]


def test_history_attached_and_missing_reported(db, world):
    history = [
        HistoryEntry(league="l1", gameweek=1, text="Tekst jeden"),
        HistoryEntry(league="l1", gameweek=2, text="Tekst dwa"),
    ]
    built = build(db, history)
    by_id = {case.id: case for case in built.cases}
    assert by_id["real-l1-gw1"].previous == []
    assert [p.gameweek for p in by_id["real-l1-gw3"].previous] == [1, 2]
    assert [p.gameweek for p in by_id["real-l1-gw2"].previous] == [1]
    assert built.missing_history == [("l2", 1), ("l2", 2)]


def test_leak_raises(db, world, monkeypatch):
    def keep_real_names(entry_ids, pool, seed, alias):
        names = dict(zip(sorted(entry_ids), pool, strict=False))
        names[880000011] = "Zenobiusz Realny"
        return names

    monkeypatch.setattr(building, "assign_pseudonyms", keep_real_names)
    with pytest.raises(PseudonymisationError) as error:
        build(db)
    assert "Zenobiusz" not in str(error.value)
    assert "real-l1" in str(error.value)


def test_real_name_in_history_raises(db, world):
    history = [HistoryEntry(league="l1", gameweek=1, text="Aurelia wygrała kolejkę")]
    with pytest.raises(PseudonymisationError) as error:
        build(db, history)
    assert "Aurelia" not in str(error.value)


def test_team_name_with_player_word_is_not_a_leak(db, world):
    built = build(db)
    assert built.cases
    picks = [pick for case in built.cases for pick in case.facts.captaincy.picks]
    assert any(pick.player == "Haaland" for pick in picks)


def test_small_pool_is_an_error(db, world):
    with pytest.raises(PseudonymisationError):
        build_real_cases(db, SEASON, [1], Pseudonyms(POOL[:2], LEAGUES), [], seed=1)


def test_word_shared_with_a_pseudonym_is_not_a_leak(db, world):
    history = [HistoryEntry(league="l1", gameweek=1, text="Liga Pierwsza, pierwsza kolejka")]
    assert build(db, history).cases
