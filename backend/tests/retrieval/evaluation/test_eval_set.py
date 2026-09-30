from app.retrieval.evaluation.dataset import (
    DEFAULT_CORPUS_PATH,
    DEFAULT_QUERIES_PATH,
    EXCLUDED_AUTHORS,
    load_corpus,
    load_queries,
)


def test_set_v1_committed_and_consistent():
    assert DEFAULT_CORPUS_PATH.exists() and DEFAULT_QUERIES_PATH.exists()
    corpus = load_corpus(DEFAULT_CORPUS_PATH)
    queries = load_queries(DEFAULT_QUERIES_PATH)

    ids = [post.x_id for post in corpus]
    assert len(corpus) >= 100
    assert len(set(ids)) == len(ids)
    assert not {post.author_handle.lower() for post in corpus} & EXCLUDED_AUTHORS

    assert 35 <= len(queries) <= 45
    assert len({query.id for query in queries}) == len(queries)
    polish = [query for query in queries if query.language == "pl"]
    assert 8 <= len(polish) <= 12
    assert {query.origin for query in queries} == {"event", "post"}
    for language in ("en", "pl"):
        assert {q.split for q in queries if q.language == language} == {"dev", "test"}

    known = set(ids)
    for query in queries:
        assert query.judgements, f"{query.id} has no judgement"
        for judgement in query.judgements:
            assert judgement.x_id in known
            # the owner's review after the PR turns labels into reviewed ones
            assert "/" in judgement.labelled_by or judgement.labelled_by == "owner"
