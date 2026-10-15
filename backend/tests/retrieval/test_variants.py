import pytest
from sqlmodel import Session

from app.extraction.linking import PlayerAlias
from app.fpl.models.reference import Player
from app.retrieval.search import search
from app.retrieval.variants import (
    VARIANTS,
    PlayerNames,
    has_initial,
    load_player_names,
    variant_text,
)
from tests.corroboration.helpers import SEASON, seed_reference
from tests.retrieval.helpers import add_tweet

FERNANDES = PlayerNames("B.Fernandes", "Bruno", "Borges Fernandes", ("Bruno F",))


def test_the_variants_are_the_three_named_ones():
    assert VARIANTS == ("web_name", "full_name", "names")


def test_variant_texts_for_a_player_with_an_initial():
    assert variant_text(FERNANDES, "web_name") == "B.Fernandes"
    assert variant_text(FERNANDES, "full_name") == "Bruno Borges Fernandes"
    assert variant_text(FERNANDES, "names") == "Fernandes Bruno F"


def test_names_for_a_player_without_an_initial_is_deduplicated():
    assert variant_text(PlayerNames("Saka", "Bukayo", "Saka"), "names") == "Saka"
    assert variant_text(PlayerNames("Gabriel", "Gabriel", "dos Santos Magalhaes"), "names") == (
        "Gabriel Magalhaes"
    )
    assert variant_text(PlayerNames("Saka", aliases=("saka", "Starboy")), "names") == (
        "Saka Starboy"
    )


def test_missing_names_fall_back_to_the_web_name():
    bare = PlayerNames("Isak")
    assert variant_text(bare, "full_name") == "Isak"
    assert variant_text(bare, "names") == "Isak"
    assert variant_text(PlayerNames("B.X", "Bruno"), "full_name") == "B.X"


@pytest.mark.parametrize(
    ("web_name", "expected"),
    [("B.Fernandes", True), ("J.Timber", True), ("Saka", False), ("Gabriel", False)],
)
def test_has_initial(web_name, expected):
    assert has_initial(web_name) is expected


def test_initial_player_found_by_full_name_and_names_not_by_web_name(db):
    add_tweet(db, 1, "Bruno Fernandes is fit")
    add_tweet(db, 2, "Fernandes trained today")

    def found(variant):
        response = search(db, variant_text(FERNANDES, variant), "fulltext")
        return {r.x_id for r in response.results}

    assert found("full_name") == {1, 2}
    assert found("names") == {1, 2}
    assert found("web_name") == set()


def test_load_player_names_reads_the_player_and_its_aliases(db):
    seed_reference(db)
    with Session(db) as session:
        session.add(
            Player(
                season=SEASON,
                fpl_id=99,
                web_name="B.Fernandes",
                first_name="Bruno",
                second_name="Borges Fernandes",
                team_fpl_id=1,
                position=3,
            )
        )
        session.commit()
    aliases = [PlayerAlias("Bruno F", SEASON, 99), PlayerAlias("Other", SEASON, 1)]
    with Session(db) as session:
        names = load_player_names(session, SEASON, 99, aliases)
        assert names == PlayerNames("B.Fernandes", "Bruno", "Borges Fernandes", ("Bruno F",))
        assert load_player_names(session, SEASON, 12345, aliases) is None
        assert load_player_names(session, SEASON, 1).aliases == ()
