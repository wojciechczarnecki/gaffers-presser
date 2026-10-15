from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.retrieval.evaluation.dataset import (
    ClaimedPost,
    CorpusPost,
    EvalSet,
    EvaluationError,
    FreeQuery,
    Judgement,
    PlayerInfo,
    PlayerQuery,
    SetManifest,
    source_flags,
)
from app.retrieval.evaluation.pooling import new_posts, pool_ids, pool_set
from tests.retrieval.fakes import FakeEmbedder, RecordingTracer
from tests.retrieval.helpers import PRICES, FixedClock

T0 = datetime(2026, 10, 1, tzinfo=UTC)
T1 = datetime(2026, 10, 8, tzinfo=UTC)
WHEN = datetime(2026, 10, 3, tzinfo=UTC)
MODEL_A, MODEL_B = "fake/embed", "other/embed"
PRICE_ROWS = PRICES | {MODEL_B: PRICES[MODEL_A]}
SAKA_TEXTS = ["Saka", "Bukayo Saka"]


def _posts():
    posts = []
    for n in range(1, 31):
        text = f"Saka note {n}" if n <= 5 else f"filler {n}"
        posts.append(
            CorpusPost(
                x_id=n,
                author_handle="listed",
                text=text,
                created_at=WHEN + timedelta(minutes=n),
                is_repost=False,
                is_reply=False,
            )
        )
    return source_flags(posts, None)


CORPUS = _posts()


def embedder(model, vectors_for_fillers, query_vector):
    vectors = {text: query_vector for text in (*SAKA_TEXTS, "saka note")}
    for post in CORPUS:
        vectors[post.text] = vectors_for_fillers(post.x_id)
    return FakeEmbedder(model=model, vectors=vectors)


def model_a():
    return embedder(MODEL_A, lambda n: [1.0, 0.01 * n, 0.0], [1.0, 0.0, 0.0])


def model_b():
    return embedder(MODEL_B, lambda n: [0.0, 0.01 * (31 - n), 1.0], [0.0, 0.0, 1.0])


def player(judgements=(), claimed=()):
    return PlayerQuery(
        id="p-gw6-1",
        split="dev",
        player=PlayerInfo(
            season="2026/27",
            fpl_id=1,
            web_name="Saka",
            first_name="Bukayo",
            second_name="Saka",
            team="Arsenal",
            aliases=[],
        ),
        gameweek=6,
        deadline_at=T1,
        window_start=T0,
        window_end=T1,
        window_rule="alerts",
        previous_last_match_end=None,
        claimed=[ClaimedPost(x_id=x, created_at=WHEN, stale=False) for x in claimed],
        judgements=list(judgements),
    )


def free(judgements=()):
    return FreeQuery(
        id="q-1",
        text="saka note",
        language="en",
        origin="post",
        source_x_id=None,
        event=None,
        split="dev",
        judgements=list(judgements),
    )


def judged(ids, by="j"):
    return [Judgement(x_id=x, relevant=True, reviewed=False, labelled_by=by) for x in ids]


def eval_set(queries):
    return EvalSet("v2", SetManifest(version="v2"), CORPUS, list(queries), Path("."))


def pool(db, queries, embedders, **kwargs):
    kwargs.setdefault("depths", [20, 30])
    return pool_set(
        db,
        eval_set(queries),
        queries,
        embedders,
        k=60,
        prices=PRICE_ROWS,
        tracer=RecordingTracer(),
        clock=FixedClock(),
        **kwargs,
    )


def test_extension_adds_exactly_the_new_models_unseen_posts(eval_db):
    query = player()
    pooled_a = pool(eval_db, [query], [model_a()])["p-gw6-1"]
    # the cut shows full-text hits and model A's nearest fillers
    assert set(range(1, 6)) <= set(pooled_a)
    assert not set(range(21, 31)) & set(pooled_a)

    judged_query = player(judged(pooled_a))
    pooled_ab = pool(eval_db, [judged_query], [model_a(), model_b()])["p-gw6-1"]
    assert set(pooled_a) <= set(pooled_ab)
    added = new_posts(judged_query, pooled_ab)
    assert set(added) == set(range(21, 31))
    assert not set(added) & {j.x_id for j in judged_query.judgements}
    assert judged_query.judgements == player(judged(pooled_a)).judgements


def test_second_extension_with_same_models_adds_nothing(eval_db):
    first = player()
    pooled = pool(eval_db, [first], [model_a(), model_b()])["p-gw6-1"]
    done = player(judged(pooled))
    again = pool(eval_db, [done], [model_a(), model_b()])["p-gw6-1"]
    assert set(again) == set(pooled)
    assert new_posts(done, again) == []


def test_the_player_pool_excludes_claimed_posts_and_deeper_depths_never_shrink_it(eval_db):
    plain = pool(eval_db, [player()], [model_a()], depths=[20])["p-gw6-1"]
    deeper = pool(eval_db, [player()], [model_a()], depths=[30])["p-gw6-1"]
    assert set(plain) <= set(deeper)
    claimed = pool(eval_db, [player(claimed=[1, 2])], [model_a()])["p-gw6-1"]
    assert not {1, 2} & set(claimed)


def test_the_free_pool_is_each_modes_top_ten(eval_db):
    pooled = pool(eval_db, [free()], [model_a()])["q-1"]
    # full text finds 1-5, the vector leg's ten nearest posts are 1-10, hybrid draws on both
    assert sorted(pooled) == list(range(1, 11))


def test_pool_ids_for_a_player_query_is_the_union_of_the_cuts():
    query = player(claimed=[1])
    rankings = {
        ("m", "fulltext", "web_name"): [1, 2, 3],
        ("m", "vector", "web_name"): [4, 1, 5],
    }
    assert pool_ids(query, rankings, 20) == [2, 3, 4, 5]
    ranking = {("m", "fulltext", ""): list(range(1, 30))}
    assert pool_ids(free(), ranking, 20) == list(range(1, 11))


def test_for_judge_returns_the_posts_that_judge_lacks():
    query = player(judged([1, 2], by="a"))
    assert new_posts(query, [1, 2, 3]) == [3]
    assert new_posts(query, [1, 2, 3], for_judge="a") == [3]
    assert new_posts(query, [1, 2, 3], for_judge="b") == [1, 2, 3]
    owner = Judgement(x_id=3, relevant=False, reviewed=True, labelled_by="owner")
    mixed = player([*judged([1], by="a"), owner])
    assert new_posts(mixed, [1, 2, 3]) == [2]
    assert new_posts(mixed, [1, 2, 3], for_judge="a") == [2, 3]


def test_the_cost_cap_is_summed_over_the_models_before_any_embedding(eval_db):
    embedders = [model_a(), model_b()]
    estimates = []
    with pytest.raises(EvaluationError, match="above --max-cost-usd"):
        pool(
            eval_db,
            [player()],
            embedders,
            max_cost_usd=1e-9,
            on_estimate=lambda model, estimate: estimates.append(model),
        )
    assert estimates == [MODEL_A, MODEL_B]
    assert all(e.calls == [] for e in embedders)
