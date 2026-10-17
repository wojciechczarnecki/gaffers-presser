from datetime import UTC, datetime, timedelta

import pytest
from typer.testing import CliRunner

from app.retrieval.evaluation.cli import app
from app.retrieval.evaluation.dataset import (
    CorpusPost,
    FreeQuery,
    Judgement,
    PlayerInfo,
    PlayerQuery,
    load_queries,
    write_corpus,
    write_queries,
)
from app.retrieval.evaluation.strata import (
    cohen_kappa,
    mentions_player,
    owner_agreement,
    pair_stratum,
    strata,
    stratified_order,
)

T0 = datetime(2026, 10, 3, 10, 0, tzinfo=UTC)
T1 = datetime(2026, 10, 10, 10, 0, tzinfo=UTC)


def j(x_id, relevant, by, reviewed=False):
    return Judgement(x_id=x_id, relevant=relevant, reviewed=reviewed, labelled_by=by)


def own(x_id, relevant):
    return j(x_id, relevant, "owner", reviewed=True)


def player_query(judgements, query_id="p-gw6-1"):
    return PlayerQuery(
        id=query_id,
        split="test",
        player=PlayerInfo(
            season="2026/27",
            fpl_id=1,
            web_name="Ødegaard",
            first_name="Martin",
            second_name="Ødegaard",
            team="Arsenal",
            aliases=["Odegaard", "Captain Martin"],
        ),
        gameweek=6,
        deadline_at=T1,
        window_start=T0,
        window_end=T1,
        window_rule="alerts",
        previous_last_match_end=None,
        claimed=[],
        judgements=list(judgements),
    )


def free_query(judgements):
    return FreeQuery(
        id="q-post-en-001",
        text="odegaard injury",
        language="en",
        origin="post",
        source_x_id=None,
        event=None,
        split="test",
        judgements=list(judgements),
    )


@pytest.mark.parametrize(
    ("text", "named"),
    [
        ("Odegaard back in training", True),
        ("ØDEGAARD OUT", True),
        ("Captain Martin starts", True),
        ("Odegaard's knee is fine", True),
        ("Odegaardo is a typo, not him", False),
        ("Saka out, Rice doubtful", False),
    ],
)
def test_the_player_is_named_by_any_name_as_a_whole_word(text, named):
    assert mentions_player(player_query([]), text) is named


def test_a_free_query_names_no_player():
    assert mentions_player(free_query([]), "Odegaard out") is False


def test_a_pair_falls_in_one_category_per_judge_set():
    q = player_query(
        [
            *[j(1, True, "a"), j(1, False, "b")],
            *[j(2, True, "a"), j(2, True, "b")],
            *[j(3, False, "a"), j(3, False, "b")],
            *[j(4, False, "a"), j(4, False, "b")],
            j(5, False, "a"),
            own(6, True),
        ]
    )
    assert pair_stratum(q, 1, "") == (("a", "b"), "disagreement")
    assert pair_stratum(q, 2, "") == (("a", "b"), "relevant")
    assert pair_stratum(q, 3, "Odegaard fit") == (("a", "b"), "named")
    assert pair_stratum(q, 4, "Saka fit") == (("a", "b"), "rest")
    assert pair_stratum(q, 5, "") == (("a",), "rest")
    assert pair_stratum(q, 6, "") is None  # the owner alone is no judge


def pilot_and_rest():
    # p-gw6-1 is a pilot query (two judges); p-gw6-2 has only the chosen judge
    pilot = [j(1, True, "a"), j(1, False, "b")]
    for x_id in range(2, 6):
        pilot += [j(x_id, True, "a"), j(x_id, True, "b")]
    for x_id in range(6, 16):
        pilot += [j(x_id, False, "a"), j(x_id, False, "b")]
    rest = [j(x_id, x_id < 104, "a") for x_id in range(100, 120)]
    return [player_query(pilot), player_query(rest, "p-gw6-2")]


TEXTS = {6: "Odegaard fit", 7: "Odegaard trains", 110: "Odegaard"} | {
    x: "other" for x in (8, 9, 10)
}


def test_the_order_is_every_disagreement_then_a_sample_per_stratum():
    queries = pilot_and_rest()
    order = stratified_order(queries, TEXTS, {"relevant": 2, "named": 9, "rest": 3}, 1)
    assert order[0] == ("p-gw6-1", 1)
    found = strata(queries, TEXTS)
    by_pair = {pair: key for key, pairs in found.items() for pair in pairs}
    counts = {}
    for pair in order:
        counts[by_pair[pair]] = counts.get(by_pair[pair], 0) + 1
    assert counts == {
        (("a", "b"), "disagreement"): 1,
        (("a", "b"), "relevant"): 2,
        (("a",), "relevant"): 2,
        (("a", "b"), "named"): 2,  # the whole stratum, smaller than the size
        (("a",), "named"): 1,
        (("a", "b"), "rest"): 3,
        (("a",), "rest"): 3,
    }
    assert stratified_order(queries, TEXTS, {"relevant": 2, "named": 9, "rest": 3}, 1) == order
    assert {tuple(stratified_order(queries, TEXTS, {"rest": 3}, seed)) for seed in range(8)} != {
        tuple(stratified_order(queries, TEXTS, {"rest": 3}, 1))
    }
    assert stratified_order(queries, TEXTS, {}, 1) == [("p-gw6-1", 1)]


def test_reviewed_pairs_are_skipped_and_the_sample_is_not_redrawn():
    queries = pilot_and_rest()
    sizes = {"rest": 3}
    full = stratified_order(queries, TEXTS, sizes, 1)
    query_id, x_id = full[1]
    marked = [
        q.model_copy(update={"judgements": [*q.judgements, own(x_id, False)]})
        if q.id == query_id
        else q
        for q in queries
    ]
    assert stratified_order(marked, TEXTS, sizes, 1) == [full[0], *full[2:]]


def test_owner_agreement_weights_each_reviewed_pair_by_its_stratum():
    # one judge, one query: 4 relevant pairs (2 reviewed, 1 wrong) and 16 others
    # (2 reviewed, 1 a missed relevant post)
    judgements = [j(x_id, x_id < 4, "a") for x_id in range(20)]
    judgements += [own(0, True), own(1, False), own(10, True), own(11, False)]
    q = player_query(judgements)
    found = owner_agreement([q], {}, "a")
    # weights: relevant 4/2 = 2, rest 16/2 = 8 -> tp 2, fp 2, fn 8, tn 8
    assert found.reviewed == 4 and found.unreviewed_pairs == 0
    assert found.precision == pytest.approx(0.5)
    assert found.recall == pytest.approx(2 / 10)
    assert found.agreement == pytest.approx(10 / 20)
    observed, expected = 0.5, (4 * 10 + 16 * 10) / 20**2
    assert found.kappa == pytest.approx((observed - expected) / (1 - expected))


def test_strata_without_a_reviewed_pair_are_counted_and_left_out():
    judgements = [j(x_id, x_id < 2, "a") for x_id in range(6)] + [own(0, True)]
    found = owner_agreement([player_query(judgements)], {}, "a")
    assert found.reviewed == 1 and found.unreviewed_pairs == 4
    assert found.precision == pytest.approx(1.0)
    assert found.recall == pytest.approx(1.0)


def test_owner_agreement_is_empty_without_reviews_and_for_another_judge():
    q = player_query([j(1, True, "a"), own(1, True)])
    empty = owner_agreement([q], {}, "b")
    assert (empty.reviewed, empty.precision, empty.recall, empty.kappa) == (0, None, None, None)


def test_cohen_kappa():
    assert cohen_kappa([]) is None
    assert cohen_kappa([(True, True), (False, False)]) == pytest.approx(1.0)
    # 0.9 observed, 0.2 * 0.1 + 0.8 * 0.9 = 0.74 by chance
    pairs = [(True, True)] + [(True, False)] + [(False, False)] * 8
    assert cohen_kappa(pairs) == pytest.approx((0.9 - 0.74) / (1 - 0.74))
    assert cohen_kappa([(False, False)] * 3) is None  # chance agreement is total


@pytest.fixture
def world(tmp_path):
    posts = [
        CorpusPost(
            x_id=x_id,
            author_handle=f"author{x_id}",
            text=TEXTS.get(x_id, f"Post {x_id}"),
            created_at=T0 + timedelta(minutes=x_id),
            is_repost=False,
            is_reply=False,
        )
        for x_id in [*range(1, 16), *range(100, 120)]
    ]
    write_corpus(tmp_path / "corpus.jsonl", posts)
    write_queries(tmp_path / "queries.jsonl", pilot_and_rest())
    return tmp_path


def test_the_review_command_reviews_by_stratum(world):
    result = CliRunner().invoke(
        app,
        ["review", "--set", str(world), "--relevant", "1", "--named", "0", "--rest", "1"],
        input="r\n" * 10,
    )
    assert result.exit_code == 0, result.output
    reviewed = {
        (q.id, e.x_id)
        for q in load_queries(world / "queries.jsonl")
        for e in q.judgements
        if e.labelled_by == "owner"
    }
    # the disagreement, then one relevant and one other pair of each judge set
    assert ("p-gw6-1", 1) in reviewed and len(reviewed) == 5
    clash = CliRunner().invoke(app, ["review", "--set", str(world), "--sample", "2", "--rest", "1"])
    assert clash.exit_code == 1 and "--sample cannot be combined" in clash.stderr


def test_the_agreement_command_prints_kappa_and_each_judge_against_the_owner(world):
    queries = load_queries(world / "queries.jsonl")
    pilot = queries[0]
    queries[0] = pilot.model_copy(
        update={"judgements": [*pilot.judgements, own(1, False), own(2, True), own(6, True)]}
    )
    write_queries(world / "queries.jsonl", queries)
    result = CliRunner().invoke(
        app, ["agreement", "--judge", "a", "--judge", "b", "--set", str(world)]
    )
    assert result.exit_code == 0, result.output
    assert "kappa: " in result.stdout and "relevant: a 5, b 4" in result.stdout
    assert "against the owner" in result.stdout
    lines = {line.split(":")[0].strip(): line for line in result.stdout.splitlines()}
    assert "pairs in unreviewed strata" in lines["a"]  # the rest query has no review
    assert "over 3 reviewed pairs" in lines["b"]
