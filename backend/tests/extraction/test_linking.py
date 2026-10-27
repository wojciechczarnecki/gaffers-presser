import logging

from app.extraction.evaluation.cases import load_cases
from app.extraction.evaluation.dataset import CASES_FILE, DEFAULT_SET_DIR, PLAYERS_FILE
from app.extraction.linking import (
    DEFAULT_ALIASES_PATH,
    PlayerAlias,
    PlayerIndex,
    PlayerRecord,
    TeamAlias,
    TeamRecord,
    load_aliases,
    load_short_first_names,
    load_snapshot,
    normalise,
)

SEASON = "2026/27"

ODEGAARD = PlayerRecord(
    season=SEASON,
    fpl_id=1,
    web_name="Ødegaard",
    first_name="Martin",
    second_name="Ødegaard",
    team_fpl_id=1,
)
SMITH_ONE = PlayerRecord(
    season=SEASON, fpl_id=2, web_name="Smith", first_name="John", second_name="Smith", team_fpl_id=1
)
SMITH_TWO = PlayerRecord(
    season=SEASON,
    fpl_id=3,
    web_name="J.Smith",
    first_name="Jake",
    second_name="Smith",
    team_fpl_id=2,
)
FERNANDES = PlayerRecord(
    season=SEASON,
    fpl_id=4,
    web_name="B.Fernandes",
    first_name="Bruno",
    second_name="Fernandes",
    team_fpl_id=1,
)

TEAM_ONE = TeamRecord(season=SEASON, fpl_id=1, name="Team One", short_name="ONE")
TEAM_TWO = TeamRecord(season=SEASON, fpl_id=2, name="Team Two", short_name="TWO")

PLAYERS = [ODEGAARD, SMITH_ONE, SMITH_TWO, FERNANDES]
TEAMS = [TEAM_ONE, TEAM_TWO]


def _index(player_aliases=(), team_aliases=()) -> PlayerIndex:
    return PlayerIndex(PLAYERS, TEAMS, list(player_aliases), list(team_aliases))


def test_accent_insensitive():
    index = _index()
    assert index.resolve("Odegaard", None) == [ODEGAARD]


def test_web_name_match():
    index = _index()
    assert index.resolve("B.Fernandes", None) == [FERNANDES]
    assert index.resolve("b.fernandes", None) == [FERNANDES]


def test_first_name_match():
    index = _index()
    assert index.resolve("bruno", None) == [FERNANDES]


def _record(fpl_id, web_name, first_name, second_name, team_fpl_id=1) -> PlayerRecord:
    return PlayerRecord(
        season=SEASON,
        fpl_id=fpl_id,
        web_name=web_name,
        first_name=first_name,
        second_name=second_name,
        team_fpl_id=team_fpl_id,
    )


JOAO_PEDRO = _record(10, "João Pedro", "João Pedro", "Junqueira de Jesus", 1)
COSTINHA = _record(11, "Costinha", "João Pedro", "Loureiro da Costa", 2)
NETO = _record(12, "Neto", "Pedro", "Lomba Neto", 1)
CAICEDO = _record(13, "Caicedo", "Moisés", "Caicedo Corozo", 1)
WHITE = _record(14, "White", "Benjamin", "White", 2)
GIBBS_WHITE = _record(15, "Gibbs-White", "Morgan", "Gibbs-White", 1)
GUIMARAES = _record(16, "Bruno G.", "Bruno", "Guimarães Rodriguez Moura", 2)
GABRIEL = _record(17, "Gabriel", "Gabriel", "dos Santos Magalhães", 2)
MARTINELLI = _record(18, "Martinelli", "Gabriel", "Martinelli Silva", 2)
SANTOS = _record(19, "Santos", "Carlos", "Santos", 1)


def _wide_index() -> PlayerIndex:
    players = [
        JOAO_PEDRO,
        COSTINHA,
        NETO,
        CAICEDO,
        WHITE,
        GIBBS_WHITE,
        GUIMARAES,
        GABRIEL,
        MARTINELLI,
        SANTOS,
    ]
    return PlayerIndex(players, TEAMS, [], [], {"benjamin": ("ben",)})


def test_first_name_with_any_surname_token_links():
    index = _wide_index()
    assert index.resolve("Moisés Caicedo", None) == [CAICEDO]
    assert index.resolve("Bruno Guimarães", None) == [GUIMARAES]


def test_surname_part_links_when_no_name_matches():
    index = _wide_index()
    assert index.resolve("Guimarães", None) == [GUIMARAES]
    assert index.resolve("Caicedo", None) == [CAICEDO]


def test_short_first_name_links_with_surname():
    index = _wide_index()
    assert index.resolve("Ben White", None) == [WHITE]
    assert index.resolve("Ben", None) == [WHITE]


def test_name_match_beats_first_name_only_match():
    index = _wide_index()
    assert index.resolve("Joao Pedro", None) == [JOAO_PEDRO]
    assert index.resolve("Gabriel", None) == [GABRIEL]


def test_web_name_beats_surname_part():
    index = _wide_index()
    assert index.resolve("Santos", None) == [SANTOS]


def test_hyphenated_surname_part_does_not_compete_with_a_whole_surname():
    index = _wide_index()
    assert index.resolve("White", None) == [WHITE]
    assert index.resolve("Gibbs-White", None) == [GIBBS_WHITE]


def test_first_name_token_keeps_every_first_name_candidate():
    index = _wide_index()
    assert index.resolve("Pedro", None) == [JOAO_PEDRO, COSTINHA, NETO]


def test_team_picks_the_best_tier_at_that_team():
    index = _wide_index()
    assert index.resolve("Pedro", "Team Two") == [COSTINHA]
    # Santos's name match plays for Team One, so Gabriel's surname part at Team Two wins.
    assert index.resolve("Santos", "Team Two") == [GABRIEL]
    assert index.resolve("Gabriel", "Team One") == [GABRIEL]


def test_name_particles_are_not_keys():
    index = _wide_index()
    assert index.resolve("dos", None) == []
    assert index.resolve("da", None) == []


def test_post_narrows_to_clubs_of_other_linked_players():
    index = _wide_index()
    resolved = index.resolve_post([("Pedro", None), ("Caicedo", None)])
    assert resolved == [[JOAO_PEDRO, NETO], [CAICEDO]]


def test_post_narrowing_keeps_candidates_without_a_club_match():
    index = _wide_index()
    resolved = index.resolve_post([("Smith", None), ("Gabriel", None), ("Nobody", None)])
    assert resolved == [[], [GABRIEL], []]
    resolved = _index().resolve_post([("Smith", None), ("Ødegaard", None)])
    assert resolved == [[SMITH_ONE], [ODEGAARD]]
    assert _index().resolve_post([("Smith", None)]) == [[SMITH_ONE, SMITH_TWO]]


def test_a_first_name_part_links_with_the_surname():
    perri = _record(20, "Perri", "Lucas Estella", "Perri", 1)
    index = PlayerIndex([perri, NETO], TEAMS, [], [], {})
    assert index.resolve("Lucas Perri", None) == [perri]
    assert index.resolve("Estella Perri", None) == [perri]


def test_a_first_name_only_link_does_not_narrow_the_post():
    index = _wide_index()
    # "Ben" links uniquely to White (Team Two) by a first name only, so Pedro stays open.
    resolved = index.resolve_post([("Pedro", None), ("Ben", None)])
    assert resolved == [[JOAO_PEDRO, COSTINHA, NETO], [WHITE]]


def test_every_tier_lists_every_candidate():
    index = _wide_index()
    assert index.resolve("Gabriel", None, every_tier=True) == [GABRIEL, MARTINELLI]
    assert index.resolve("Gabriel", "Team Two", every_tier=True) == [GABRIEL, MARTINELLI]
    assert index.resolve("Nobody", None, every_tier=True) == []


def test_committed_short_first_names_parse():
    forms = load_short_first_names()
    assert forms["benjamin"] == ("ben",)
    assert forms["jose maria"] == ("chema",)


def test_last_name_match_is_ambiguous():
    index = _index()
    assert index.resolve("Smith", None) == [SMITH_ONE, SMITH_TWO]


def test_full_name_match():
    index = _index()
    assert index.resolve("John Smith", None) == [SMITH_ONE]


def test_case_insensitive():
    index = _index()
    assert index.resolve("FERNANDES", None) == [FERNANDES]


def test_team_narrows():
    index = _index()
    assert index.resolve("Smith", "Team One") == [SMITH_ONE]
    assert index.resolve("Smith", "TWO") == [SMITH_TWO]


def test_team_filter_empty_keeps_all():
    index = _index()
    # Fernandes only plays for "Team One"; narrowing by "Team Two" would leave nothing, so
    # the whole (one-element) candidate list is kept instead.
    assert index.resolve("B.Fernandes", "Team Two") == [FERNANDES]


def test_unknown_team_no_narrowing():
    index = _index()
    assert index.resolve("Smith", "Nonexistent FC") == [SMITH_ONE, SMITH_TWO]


def test_no_match_gives_empty_list():
    index = _index()
    assert index.resolve("Nobody", None) == []


def test_alias_links():
    index = _index(player_aliases=[PlayerAlias(alias="Bruno F.", season=SEASON, fpl_id=4)])
    assert index.resolve("Bruno F.", None) == [FERNANDES]


def test_team_alias_narrows():
    index = _index(team_aliases=[TeamAlias(alias="The Ones", short_name="ONE")])
    assert index.resolve("Smith", "The Ones") == [SMITH_ONE]


def test_stale_alias_ignored_and_logged_once(caplog):
    stale = PlayerAlias(alias="Ghost", season=SEASON, fpl_id=999)
    with caplog.at_level(logging.WARNING, logger="app.extraction.linking"):
        first = PlayerIndex(PLAYERS, TEAMS, [stale], [])
        second = PlayerIndex(PLAYERS, TEAMS, [stale], [])
    assert first.resolve("Ghost", None) == []
    assert second.resolve("Ghost", None) == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1


def test_normalise_strips_accents_and_punctuation():
    assert normalise("Ødegaard") == "odegaard"
    assert normalise("Wan-Bissaka") == "wan bissaka"
    assert normalise("D'Ath") == "d ath"
    assert normalise("  Multiple   spaces ") == "multiple spaces"


def test_committed_aliases_parse():
    player_aliases, team_aliases = load_aliases(DEFAULT_ALIASES_PATH)
    assert player_aliases, "expected at least one committed player alias"
    assert team_aliases, "expected at least one committed team alias"
    for alias in player_aliases:
        assert alias.alias
        assert alias.season
        assert isinstance(alias.fpl_id, int)
    for alias in team_aliases:
        assert alias.alias
        assert alias.short_name


def test_player_by_fpl_id():
    index = _index()
    assert index.player(4) == FERNANDES
    assert index.player(99) is None
    assert [t.fpl_id for t in index.teams()] == [1, 2]


def test_committed_savinho_alias_links_savio():
    players, teams = load_snapshot(DEFAULT_SET_DIR / PLAYERS_FILE)
    index = PlayerIndex(players, teams, *load_aliases(DEFAULT_ALIASES_PATH))
    [player] = index.resolve("Savinho", None)
    assert (player.fpl_id, player.web_name) == (403, "Sávio")


def test_reviewed_set_v2_labels_link():
    players, teams = load_snapshot(DEFAULT_SET_DIR / PLAYERS_FILE)
    index = PlayerIndex(players, teams, *load_aliases(DEFAULT_ALIASES_PATH))
    unique = ambiguous = 0
    lost = []
    for case in load_cases(DEFAULT_SET_DIR / CASES_FILE):
        events = [e for e in case.expected_events if case.reviewed and e.fpl_id is not None]
        resolved = index.resolve_post([(event.mention, None) for event in events])
        for event, candidates in zip(events, resolved, strict=True):
            ids = [candidate.fpl_id for candidate in candidates]
            if ids == [event.fpl_id]:
                unique += 1
            elif event.fpl_id in ids:
                ambiguous += 1
            else:
                lost.append((case.id, event.mention, event.fpl_id, ids))
    assert lost == []
    # Measured 2026-10-27 (BACKLOG #57): 439 unique, 11 left to link_disambiguation.
    assert unique >= 439
    assert ambiguous <= 11
