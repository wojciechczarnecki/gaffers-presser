import logging

from app.extraction.linking import (
    DEFAULT_ALIASES_PATH,
    PlayerAlias,
    PlayerIndex,
    PlayerRecord,
    TeamAlias,
    TeamRecord,
    load_aliases,
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
