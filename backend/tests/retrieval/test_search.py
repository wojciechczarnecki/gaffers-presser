import logging
from datetime import timedelta

import pytest
from sqlmodel import Session

from app.retrieval.search import (
    NoEmbeddingsError,
    SearchError,
    SearchFilters,
    search,
)
from app.retrieval.store import save_embedded
from tests.retrieval.fakes import AlwaysFailingEmbedder, FakeEmbedder, RecordingTracer
from tests.retrieval.helpers import MODEL, NOW, PRICES, add_tweet

QUERY = "saka injury"


def _embed(db, x_id: int, vector: list[float], model: str = MODEL) -> None:
    with Session(db) as session, session.begin():
        save_embedded(
            session,
            x_id=x_id,
            model=model,
            vector=vector,
            input_tokens=5,
            cost_usd=None,
            latency_seconds=None,
            attempts=1,
            now=NOW,
        )


def _ids(response) -> list[int]:
    return [result.x_id for result in response.results]


def _search(db, query, mode="hybrid", embedder=None, **kwargs):
    return search(db, query, mode, embedder=embedder, prices=PRICES, **kwargs)


def _fulltext_corpus(db) -> None:
    add_tweet(db, 1, "Saka picked up an injury")
    add_tweet(db, 2, "Haaland scored a hat-trick")
    add_tweet(db, 3, "Ødegaard is back in training")


def test_fulltext_injured_finds_injury(db):
    _fulltext_corpus(db)
    response = _search(db, "injured", "fulltext")
    assert _ids(response) == [1]
    assert response.mode == "fulltext"
    assert response.model is None


def test_fulltext_odegaard_finds_accented(db):
    _fulltext_corpus(db)
    assert _ids(_search(db, "Odegaard", "fulltext")) == [3]


def test_fulltext_stop_words_only_returns_nothing(db):
    _fulltext_corpus(db)
    assert _ids(_search(db, "the out of", "fulltext")) == []
    assert _ids(_search(db, "", "fulltext")) == []


def test_fulltext_ranks_more_matching_terms_higher(db):
    add_tweet(db, 1, "Saka scores again")
    add_tweet(db, 2, "Saka injury doubt for Arsenal")
    assert _ids(_search(db, "saka injury", "fulltext"))[0] == 2


def test_fulltext_query_with_quotes_and_backslashes_does_not_fail(db):
    add_tweet(db, 1, "O'Neil said it's fine")
    assert _ids(_search(db, "o'neil \\ fine", "fulltext")) == [1]


def _vector_corpus(db) -> None:
    for x_id, text in [(1, "one"), (2, "two"), (3, "three"), (4, "four")]:
        add_tweet(db, x_id, text)
    _embed(db, 1, [1.0, 0.0, 0.0])
    _embed(db, 2, [0.8, 0.6, 0.0])
    _embed(db, 3, [0.0, 1.0, 0.0])
    _embed(db, 4, [1.0, 0.0, 0.0], model="other/embed")


def test_vector_orders_by_cosine_within_model(db):
    _vector_corpus(db)
    embedder = FakeEmbedder(vectors={"q": [1.0, 0.0, 0.0]})
    response = _search(db, "q", "vector", embedder)
    assert _ids(response) == [1, 2, 3]
    assert response.model == MODEL
    assert 4 not in _ids(response)
    assert [r.ranks for r in response.results][0] == {"fulltext": None, "vector": 1}


def test_vector_without_embeddings_errors_clearly(db):
    add_tweet(db, 1)
    with pytest.raises(NoEmbeddingsError, match="no embeddings for model fake/embed"):
        _search(db, "q", "vector", FakeEmbedder())
    with pytest.raises(NoEmbeddingsError, match="app.retrieval index --model fake/embed"):
        _search(db, "q", "hybrid", FakeEmbedder())


def _hybrid_corpus(db) -> None:
    add_tweet(db, 1, "Saka injury doubt", created_at=NOW - timedelta(hours=4))
    add_tweet(db, 2, "Saka scores again", created_at=NOW - timedelta(hours=3))
    add_tweet(db, 3, "injury crisis at Chelsea", created_at=NOW - timedelta(hours=2))
    add_tweet(db, 4, "Haaland hat-trick", created_at=NOW - timedelta(hours=1))
    _embed(db, 1, [0.3, 0.9, 0.3])
    _embed(db, 2, [0.6, 0.8, 0.0])
    _embed(db, 3, [0.0, 0.0, 1.0])
    _embed(db, 4, [1.0, 0.0, 0.0])


def test_hybrid_order_equals_hand_computed_rrf(db):
    _hybrid_corpus(db)
    embedder = FakeEmbedder(vectors={QUERY: [1.0, 0.0, 0.0]})

    fulltext = _search(db, QUERY, "fulltext")
    vector = _search(db, QUERY, "vector", embedder)
    # post 4 has no lexical match; post 3 (injury only) is a lexical match, orthogonal to
    # the query vector, so it is last in the vector leg
    assert _ids(fulltext)[0] == 1
    assert sorted(_ids(fulltext)) == [1, 2, 3]
    assert _ids(vector) == [4, 2, 1, 3]

    ft = {x_id: rank for rank, x_id in enumerate(_ids(fulltext), start=1)}
    vec = {x_id: rank for rank, x_id in enumerate(_ids(vector), start=1)}
    expected = {
        x_id: (1 / (60 + ft[x_id]) if x_id in ft else 0) + (1 / (60 + vec[x_id])) for x_id in vec
    }
    expected_order = sorted(expected, key=lambda x_id: (-expected[x_id], x_id))

    hybrid = _search(db, QUERY, "hybrid", embedder)

    assert [r.x_id for r in hybrid.results] == expected_order
    for result in hybrid.results:
        assert result.score == pytest.approx(expected[result.x_id])
        assert result.ranks == {"fulltext": ft.get(result.x_id), "vector": vec[result.x_id]}
    assert hybrid.failed_legs == ()


def test_hybrid_post_found_by_only_one_mode_can_appear(db):
    _hybrid_corpus(db)
    embedder = FakeEmbedder(vectors={QUERY: [1.0, 0.0, 0.0]})
    hybrid = _search(db, QUERY, "hybrid", embedder)
    by_id = {r.x_id: r for r in hybrid.results}
    assert by_id[4].ranks["fulltext"] is None
    assert by_id[4].ranks["vector"] == 1


def test_k_and_depth_overridable(db):
    _hybrid_corpus(db)
    embedder = FakeEmbedder(vectors={QUERY: [1.0, 0.0, 0.0]})

    shallow = _search(db, QUERY, "hybrid", embedder, depth=1)
    assert sorted(_ids(shallow)) == [1, 4]
    assert all(sum(rank is not None for rank in r.ranks.values()) == 1 for r in shallow.results)

    default = {r.x_id: r.score for r in _search(db, QUERY, "hybrid", embedder).results}
    low_k = {r.x_id: r.score for r in _search(db, QUERY, "hybrid", embedder, k=1).results}
    assert low_k[4] == pytest.approx(1 / 2)
    assert default[4] == pytest.approx(1 / 61)


def test_filters_limit_window_reposts_replies(db):
    add_tweet(db, 1, "Saka injury one", created_at=NOW - timedelta(days=3))
    add_tweet(db, 2, "Saka injury two", created_at=NOW - timedelta(days=2), is_repost=True)
    add_tweet(db, 3, "Saka injury three", created_at=NOW - timedelta(days=1), is_reply=True)
    add_tweet(db, 4, "Saka injury four", created_at=NOW - timedelta(hours=1))

    assert sorted(_ids(_search(db, "saka", "fulltext"))) == [1, 2, 3, 4]
    assert len(_ids(_search(db, "saka", "fulltext", limit=2))) == 2
    assert sorted(
        _ids(_search(db, "saka", "fulltext", filters=SearchFilters(exclude_reposts=True)))
    ) == [
        1,
        3,
        4,
    ]
    assert sorted(
        _ids(_search(db, "saka", "fulltext", filters=SearchFilters(exclude_replies=True)))
    ) == [
        1,
        2,
        4,
    ]
    window = SearchFilters(since=NOW - timedelta(days=2), until=NOW - timedelta(days=1))
    assert _ids(_search(db, "saka", "fulltext", filters=window)) == [2]


def test_filters_apply_to_the_vector_leg_too(db):
    add_tweet(db, 1, "one", is_repost=True)
    add_tweet(db, 2, "two", created_at=NOW - timedelta(days=5))
    add_tweet(db, 3, "three")
    for x_id in (1, 2, 3):
        _embed(db, x_id, [1.0, 0.0, 0.0])
    embedder = FakeEmbedder(default=[1.0, 0.0, 0.0])
    filters = SearchFilters(exclude_reposts=True, since=NOW - timedelta(days=1))
    assert _ids(_search(db, "q", "vector", embedder, filters=filters)) == [3]


def test_result_fields_and_ranks(db):
    add_tweet(db, 5, "Saka injury update", author="fabrizio", created_at=NOW - timedelta(hours=2))
    response = _search(db, "saka", "fulltext")
    (result,) = response.results
    assert result.x_id == 5
    assert result.author_handle == "fabrizio"
    assert result.created_at == NOW - timedelta(hours=2)
    assert result.text == "Saka injury update"
    assert result.score == pytest.approx(1 / 61)
    assert result.ranks == {"fulltext": 1, "vector": None}


def test_hybrid_degrades_to_fulltext_when_embedding_fails(db, caplog):
    _hybrid_corpus(db)
    with caplog.at_level(logging.ERROR):
        response = _search(db, QUERY, "hybrid", AlwaysFailingEmbedder())
    assert response.failed_legs == ("vector",)
    assert sorted(_ids(response)) == [1, 2, 3]
    assert all(r.ranks["vector"] is None and r.ranks["fulltext"] for r in response.results)
    assert "vector leg failed: RuntimeError" in caplog.text


def test_vector_fails_clearly_when_embedding_fails(db):
    _hybrid_corpus(db)
    with pytest.raises(SearchError, match="query embedding failed: RuntimeError"):
        _search(db, QUERY, "vector", AlwaysFailingEmbedder())


def test_search_traced_with_ids_per_leg_and_failed_legs(db):
    _hybrid_corpus(db)
    tracer = RecordingTracer()
    embedder = FakeEmbedder(vectors={QUERY: [1.0, 0.0, 0.0]})
    response = _search(db, QUERY, "hybrid", embedder, tracer=tracer)
    (record,) = tracer.searches
    assert record["query"] == QUERY
    assert record["mode"] == "hybrid"
    assert record["model"] == MODEL
    assert record["ids_by_mode"]["vector"] == [4, 2, 1, 3]
    assert record["ids_by_mode"]["hybrid"] == _ids(response)
    assert record["failed_legs"] == ()
    (embedding,) = tracer.embeddings
    assert embedding["texts"] == [QUERY]


def test_fulltext_mode_never_calls_the_embedder(db):
    _fulltext_corpus(db)
    embedder = FakeEmbedder()
    _search(db, "injured", "fulltext", embedder)
    assert embedder.calls == []
