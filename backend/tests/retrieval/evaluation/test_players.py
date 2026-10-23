from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, delete
from sqlmodel import Session
from typer.testing import CliRunner

from app.corroboration.schemas import PlayerRef
from app.corroboration.sources import sql_claims
from app.fpl.models.reference import Fixture
from app.retrieval.evaluation.cli import app
from app.retrieval.evaluation.dataset import (
    CorpusPost,
    EvaluationError,
    FreeQuery,
    Judgement,
    PlayerInfo,
    PlayerQuery,
    load_queries,
    write_corpus,
    write_queries,
)
from app.retrieval.evaluation.players import (
    assign_splits,
    build_player_slice,
    gameweek_window,
    merge_player_queries,
)
from tests.corroboration.helpers import ISAK, SAKA, SEASON, add_extraction, seed_reference
from tests.retrieval.helpers import add_tweet
from tests.retrieval.test_cli import _deps
from tests.tweets.membership_helpers import set_members

D5 = datetime(2026, 9, 26, 17, 30, tzinfo=UTC)
D6 = datetime(2026, 10, 3, 17, 30, tzinfo=UTC)


def add_fixture(db, fpl_id, gameweek, kickoff):
    with Session(db) as session:
        session.add(
            Fixture(
                season=SEASON,
                fpl_id=fpl_id,
                gameweek_fpl_id=gameweek,
                kickoff_at=kickoff,
                team_h_fpl_id=1,
                team_a_fpl_id=2,
                finished=True,
            )
        )
        session.commit()


def window(db, rule, gameweek=6):
    with Session(db) as session:
        return gameweek_window(session, SEASON, gameweek, rule)


def test_alerts_window_starts_at_the_previous_deadline(db):
    seed_reference(db, {5: D5, 6: D6})
    found = window(db, "alerts")
    assert (found.window_start, found.window_end, found.deadline_at) == (D5, D6, D6)
    assert found.rule == "alerts"
    assert found.previous_last_match_end is None


def test_a_break_longer_than_the_lookback_starts_at_the_deadline_minus_the_lookback(db):
    far = D6 - timedelta(days=14)
    seed_reference(db, {5: far, 6: D6})
    assert window(db, "alerts").window_start == D6 - timedelta(days=7)


def test_the_first_gameweek_has_no_previous_deadline(db):
    seed_reference(db, {6: D6})
    assert window(db, "alerts").window_start == D6 - timedelta(days=7)


def test_after_last_match_starts_two_hours_after_the_latest_kickoff(db):
    seed_reference(db, {5: D5, 6: D6})
    add_fixture(db, 1, 5, datetime(2026, 9, 27, 14, 0, tzinfo=UTC))
    add_fixture(db, 2, 5, datetime(2026, 9, 29, 19, 0, tzinfo=UTC))
    add_fixture(db, 3, 5, None)
    add_fixture(db, 4, 6, datetime(2026, 10, 4, 14, 0, tzinfo=UTC))
    after = window(db, "after-last-match")
    assert after.window_start == datetime(2026, 9, 29, 21, 0, tzinfo=UTC)
    assert after.previous_last_match_end == after.window_start
    alerts = window(db, "alerts")
    assert alerts.previous_last_match_end == after.window_start


def test_after_last_match_without_a_fixture_names_the_previous_gameweek(db):
    seed_reference(db, {5: D5, 6: D6})
    with pytest.raises(EvaluationError, match="gameweek 5"):
        window(db, "after-last-match")


def test_an_unknown_gameweek_is_an_error(db):
    seed_reference(db, {6: D6})
    with pytest.raises(EvaluationError, match="gameweek 9"):
        window(db, "alerts", gameweek=9)


LAST_KICKOFF = datetime(2026, 9, 29, 19, 0, tzinfo=UTC)
LAST_END = LAST_KICKOFF + timedelta(hours=2)
LINEUP_AT = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
IN_WINDOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def world(db, deadlines=None):
    seed_reference(db, deadlines or {5: D5, 6: D6})
    add_fixture(db, 1, 5, datetime(2026, 9, 27, 14, 0, tzinfo=UTC))
    add_fixture(db, 2, 5, LAST_KICKOFF)
    set_members(db, ["listed"])


def claim(db, x_id, when, player=SAKA, author="listed"):
    add_tweet(db, x_id, f"post {x_id}", created_at=when, author=author)
    add_extraction(db, x_id, player)


def build(db, rule="alerts"):
    return build_player_slice(db, SEASON, 6, rule, seed=1)


def test_a_player_claimed_twice_gives_one_query_with_two_claimed_ids(db):
    world(db)
    claim(db, 1, IN_WINDOW)
    claim(db, 2, IN_WINDOW + timedelta(hours=1))
    queries = build(db)
    assert [q.id for q in queries] == ["p-gw6-1"]
    assert [c.x_id for c in queries[0].claimed] == [1, 2]
    assert queries[0].player.web_name == "Saka"
    assert queries[0].player.team == "Arsenal"
    assert (queries[0].gameweek, queries[0].window_end) == (6, D6)


def test_a_post_outside_the_window_is_not_claimed(db):
    world(db)
    claim(db, 1, IN_WINDOW)
    claim(db, 2, D6 + timedelta(hours=1))
    claim(db, 3, D5 - timedelta(hours=1))
    assert [c.x_id for c in build(db)[0].claimed] == [1]


def test_an_off_list_post_is_not_a_source(db):
    world(db)
    claim(db, 1, IN_WINDOW)
    claim(db, 2, IN_WINDOW, author="other")
    assert [c.x_id for c in build(db)[0].claimed] == [1]


def test_a_player_with_no_event_gets_no_query(db):
    world(db)
    claim(db, 1, IN_WINDOW)
    add_tweet(db, 2, "no player here", created_at=IN_WINDOW, author="listed")
    add_extraction(db, 2, None)
    claim(db, 3, IN_WINDOW, player=None)
    assert [q.player.fpl_id for q in build(db)] == [SAKA]


def test_a_lineup_before_the_last_match_is_outside_both_rules(db):
    world(db)
    claim(db, 1, LINEUP_AT)
    claim(db, 2, IN_WINDOW)
    for rule in ("alerts", "after-last-match"):
        found = build(db, rule)[0]
        assert [(c.x_id, c.stale) for c in found.claimed] == [(2, False)]
        assert found.window_start == LAST_END
        assert found.window_rule == rule


def test_alerts_window_follows_the_production_floor(db):
    world(db)
    assert window(db, "alerts").window_start == LAST_END
    other = window(db, "alerts", gameweek=5)
    assert other.window_start == D5 - timedelta(days=7)
    with Session(db) as session:
        session.exec(delete(Fixture))
        session.commit()
    assert window(db, "alerts").window_start == D5


def test_a_window_longer_than_the_lookback_starts_at_the_deadline_minus_the_lookback(db):
    seed_reference(db, {5: D6 - timedelta(days=14), 6: D6})
    set_members(db, ["listed"])
    claim(db, 1, D6 - timedelta(days=10))
    claim(db, 2, D6 - timedelta(days=6))
    queries = build(db)
    assert queries[0].window_start == D6 - timedelta(days=7)
    assert [c.x_id for c in queries[0].claimed] == [2]


def test_claimed_ids_equal_sql_claims(db):
    world(db)
    claim(db, 1, LINEUP_AT)
    claim(db, 2, IN_WINDOW)
    claim(db, 3, IN_WINDOW, player=ISAK)
    claim(db, 4, IN_WINDOW, author="other")
    queries = build(db)
    assert len(queries) == 2
    assert {c.x_id for q in queries for c in q.claimed} == {2, 3}
    for query in queries:
        ref = PlayerRef(SEASON, query.player.fpl_id, query.player.web_name, query.player.team)
        with Session(db) as session:
            expected = {c.post.x_id for c in sql_claims(session, ref, query.window_start, D6)}
        assert {c.x_id for c in query.claimed} == expected


def test_builder_runs_on_a_read_only_connection(db):
    world(db)
    claim(db, 1, IN_WINDOW)
    read_only = create_engine(
        db.url, connect_args={"options": "-c default_transaction_read_only=on"}
    )
    try:
        assert len(build_player_slice(read_only, SEASON, 6, "alerts", seed=1)) == 1
    finally:
        read_only.dispose()


# assign_splits(10 queries, seed=1): DEV_SHARE 0.3 puts three of them in dev
FIXED_SPLITS = ["test"] * 6 + ["dev", "test", "dev", "dev"]


def test_splits_are_seeded_and_cover_both_slices(db):
    world(db)
    for n in range(10):
        add_tweet(db, 100 + n, f"post {n}", created_at=IN_WINDOW, author="listed")
        add_extraction(db, 100 + n, SAKA if n == 0 else None)
    seed_players = build(db)
    assert seed_players[0].split in ("dev", "test")
    again = build_player_slice(db, SEASON, 6, "alerts", seed=1)
    assert [q.split for q in again] == [q.split for q in seed_players]

    many = [seed_players[0].model_copy(update={"id": f"p-gw6-{n}"}) for n in range(10)]
    splits = [q.split for q in assign_splits(many, seed=1)]
    assert splits == FIXED_SPLITS
    assert splits.count("dev") == 3
    assert [q.split for q in assign_splits(many, seed=1)] == splits
    assert any([q.split for q in assign_splits(many, seed=s)] != splits for s in (2, 3, 4))


def test_the_cli_writes_player_queries_keeps_free_ones_and_refuses_existing(db, tmp_path):
    world(db)
    claim(db, 1, LINEUP_AT)
    claim(db, 2, IN_WINDOW)
    free = FreeQuery(
        id="q-1",
        text="x",
        language="en",
        origin="post",
        source_x_id=None,
        event=None,
        split="test",
        judgements=[Judgement(x_id=1, relevant=True, reviewed=False, labelled_by="m")],
    )
    write_queries(tmp_path / "queries.jsonl", [free])
    write_corpus(
        tmp_path / "corpus.jsonl",
        [
            CorpusPost(
                x_id=1,
                author_handle="listed",
                text="t",
                created_at=LINEUP_AT,
                is_repost=False,
                is_reply=False,
                is_source=True,
            )
        ],
    )
    args = ["build-player-slice", "--gameweek", "6", "--set", str(tmp_path)]
    result = CliRunner().invoke(app, args, obj=_deps(db))
    assert result.exit_code == 0, result.output
    assert "player queries: 1" in result.stdout
    assert "claimed posts: 1  stale: 0" in result.stdout
    assert "players with an initial: 0" in result.stdout
    assert "1 claimed posts are not source posts" in result.stdout
    loaded = load_queries(tmp_path / "queries.jsonl")
    assert [type(q) for q in loaded] == [PlayerQuery, FreeQuery]
    assert loaded[1] == free

    again = CliRunner().invoke(app, args, obj=_deps(db))
    assert again.exit_code == 1
    assert "--force" in again.stderr
    forced = CliRunner().invoke(
        app, [*args, "--force", "--window", "after-last-match"], obj=_deps(db)
    )
    assert forced.exit_code == 0, forced.output
    assert [c.x_id for c in load_queries(tmp_path / "queries.jsonl")[0].claimed] == [2]

    bad = CliRunner().invoke(app, [*args, "--window", "nonsense"], obj=_deps(db))
    assert bad.exit_code == 1


def test_a_gameweek_is_added_beside_the_others_and_replaces_only_its_own():
    def query(gameweek, fpl_id, split):
        return PlayerQuery(
            id=f"p-gw{gameweek}-{fpl_id}",
            split=split,
            player=PlayerInfo(
                season="2026/27",
                fpl_id=fpl_id,
                web_name="Saka",
                first_name="Bukayo",
                second_name="Saka",
                team="Arsenal",
                aliases=[],
            ),
            gameweek=gameweek,
            deadline_at=LINEUP_AT,
            window_start=LINEUP_AT,
            window_end=LINEUP_AT,
            window_rule="alerts",
            previous_last_match_end=None,
            claimed=[],
            judgements=[],
        )

    free = FreeQuery(
        id="q-post-en-001",
        text="saka",
        language="en",
        origin="post",
        source_x_id=None,
        event=None,
        split="test",
        judgements=[],
    )
    gw6 = [query(6, 1, "dev"), query(6, 2, "test")]
    added = merge_player_queries([*gw6, free], [query(7, 1, "test")], 7)
    assert [q.id for q in added] == ["p-gw6-1", "p-gw6-2", "p-gw7-1", "q-post-en-001"]
    assert added[:2] == gw6  # the earlier gameweek keeps its split
    replaced = merge_player_queries(added, [query(7, 3, "dev")], 7)
    assert [q.id for q in replaced] == ["p-gw6-1", "p-gw6-2", "p-gw7-3", "q-post-en-001"]
