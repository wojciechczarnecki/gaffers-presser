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
    STRATA_FILE,
    cohen_kappa,
    load_strata,
    mentions_player,
    owner_agreement,
    pair_stratum,
    save_strata,
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
        ("Brak Ødegaarda na treningu", True),
        ("z Odegaardem w składzie", True),
        ("Odegaardowski is someone else", False),
        ("NotOdegaard", False),
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


def test_the_judges_compare_against_the_owner_on_the_pairs_both_labelled():
    queries = pilot_and_rest()
    pilot = queries[0]
    queries[0] = pilot.model_copy(update={"judgements": [*pilot.judgements, own(2, True)]})
    rest = queries[1]
    queries[1] = rest.model_copy(update={"judgements": [*rest.judgements, own(100, False)]})
    alone = owner_agreement(queries, TEXTS, "a")
    shared = owner_agreement(queries, TEXTS, "a", within=("a", "b"))
    assert alone.reviewed == 2 and alone.precision == pytest.approx(4 / 8)
    assert shared.reviewed == 1 and shared.precision == pytest.approx(1.0)


def test_reviewed_entries_are_not_a_judge_label_between_judges():
    from app.retrieval.evaluation.judging import agreement

    q = player_query([j(1, True, "a"), j(1, False, "b"), j(1, True, "b", reviewed=True)])
    assert agreement([q], "a", "b").pairs == [(True, False)]


def relabel(query, x_id, relevant, by="a"):
    kept = [e for e in query.judgements if not (e.x_id == x_id and e.labelled_by == by)]
    return query.model_copy(update={"judgements": [*kept, j(x_id, relevant, by)]})


def test_frozen_strata_keep_a_rejudged_pair_where_it_was_sampled():
    # 4 relevant pairs (2 reviewed) and 16 others (2 reviewed), then the judge wrongly turns a
    # reviewed relevant pair to not relevant
    judgements = [j(x_id, x_id < 4, "a") for x_id in range(20)]
    judgements += [own(0, True), own(1, True), own(10, False), own(11, False)]
    q = player_query(judgements)
    frozen = strata([q], {})
    changed = relabel(q, 1, False)
    # the weights stay relevant 4/2 and rest 16/2: tp 2, fn 2, tn 16
    found = owner_agreement([changed], {}, "a", frozen=frozen)
    assert found.recall == pytest.approx(0.5) and found.precision == pytest.approx(1.0)
    # unfrozen, the miss would weigh 17/3 in the other stratum
    assert owner_agreement([changed], {}, "a").recall == pytest.approx(3 / (3 + 17 / 3))
    assert strata([changed], {}) != frozen and strata([changed], {}, frozen) == frozen


def test_pairs_labelled_after_freezing_form_new_strata():
    q = player_query([j(x_id, x_id < 2, "a") for x_id in range(6)] + [own(0, True), own(3, False)])
    frozen = strata([q], {})
    later = q.model_copy(update={"judgements": [*q.judgements, j(50, True, "a")]})
    found = strata([later], {}, frozen)
    assert found[(("a",), "relevant (new)")] == [("p-gw6-1", 50)]
    assert owner_agreement([later], {}, "a", frozen=frozen).unreviewed_pairs == 1
    assert stratified_order([later], {}, {"relevant": 5}, 1, frozen) == [
        ("p-gw6-1", 1),
        ("p-gw6-1", 50),
    ]


def test_strata_round_trip_through_the_file(tmp_path):
    found = strata(pilot_and_rest(), TEXTS)
    save_strata(tmp_path / STRATA_FILE, found)
    assert load_strata(tmp_path / STRATA_FILE) == found
    assert load_strata(tmp_path / "missing.json") is None


def test_the_review_freezes_the_player_strata_and_skips_free_queries(world):
    queries = load_queries(world / "queries.jsonl")
    free = free_query([j(1, True, "a")])
    write_queries(world / "queries.jsonl", [*queries, free])
    args = ["review", "--set", str(world), "--relevant", "1", "--named", "0", "--rest", "0"]
    first = CliRunner().invoke(app, args, input="q\n")
    assert first.exit_code == 0, first.output
    assert "q-post-en-001" not in first.output
    frozen = load_strata(world / STRATA_FILE)
    assert all(not q.startswith("q-") for pairs in frozen.values() for q, _ in pairs)
    # a re-judge after the first session leaves the saved strata as they were
    queries = load_queries(world / "queries.jsonl")
    queries[1] = relabel(queries[1], 100, False)
    write_queries(world / "queries.jsonl", queries)
    second = CliRunner().invoke(app, args, input="q\n")
    assert second.exit_code == 0, second.output
    assert "covers the player slice" in second.output
    assert load_strata(world / STRATA_FILE) == frozen


def test_a_pair_whose_judges_changed_after_freezing_falls_in_a_new_stratum():
    q = player_query([j(x_id, x_id < 2, "a") for x_id in range(6)])
    frozen = strata([q], {})
    later = q.model_copy(update={"judgements": [*q.judgements, j(4, False, "b")]})
    found = strata([later], {}, frozen)
    assert found[(("a", "b"), "rest (new)")] == [("p-gw6-1", 4)]
    assert ("p-gw6-1", 4) not in found[(("a",), "rest")]
    assert owner_agreement([later], {}, "b", frozen=frozen).unreviewed_pairs == 1


def test_a_pair_that_turned_into_a_disagreement_is_always_shown():
    pilot = [j(x_id, True, by) for x_id in (1, 2, 3) for by in ("a", "b")]
    q = player_query(pilot)
    frozen = strata([q], {})
    later = relabel(q, 2, False, by="b")
    assert strata([later], {}, frozen)[(("a", "b"), "disagreement (new)")] == [("p-gw6-1", 2)]
    assert stratified_order([later], {}, {"relevant": 0}, 1, frozen) == [("p-gw6-1", 2)]


def test_frozen_pairs_without_a_judge_label_are_dropped_and_free_pairs_never_new():
    q = player_query([j(1, True, "a"), j(2, False, "a")])
    frozen = strata([q], {})
    rebuilt = q.model_copy(update={"judgements": [j(1, True, "a")]})
    free = free_query([j(7, True, "a")])
    found = strata([rebuilt, free], {}, frozen)
    assert found == {
        (("a",), "relevant"): [("p-gw6-1", 1)],
        (("a",), "relevant (free)"): [("q-post-en-001", 7)],
    }


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '{"strata": [{"judges": ["a"]}]}',
        '{"strata": [{"judges": ["a"], "category": "rest", "pairs": [3]}]}',
    ],
)
def test_a_damaged_strata_file_is_an_evaluation_error(tmp_path, content):
    from app.retrieval.evaluation.dataset import EvaluationError

    (tmp_path / STRATA_FILE).write_text(content)
    with pytest.raises(EvaluationError, match="cannot read strata.json"):
        load_strata(tmp_path / STRATA_FILE)
