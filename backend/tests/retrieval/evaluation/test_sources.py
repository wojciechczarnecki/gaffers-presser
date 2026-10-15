from datetime import UTC, datetime

import pytest

from app.retrieval.evaluation.dataset import CorpusPost, EvaluationError, source_flags
from app.retrieval.evaluation.schema import load_eval_corpus
from app.retrieval.search import SearchFilters, search

WHEN = datetime(2026, 10, 1, tzinfo=UTC)


def post(x_id, author, quoted=None, text="Saka news today"):
    return CorpusPost(
        x_id=x_id,
        author_handle=author,
        text=text,
        created_at=WHEN,
        is_repost=False,
        is_reply=False,
        quoted_x_id=quoted,
    )


def mixed_corpus():
    return [
        post(1, "Listed"),
        post(2, "other"),
        post(3, "other"),
        post(4, "listed", quoted=3),
        post(5, "other"),
        post(6, "other", quoted=5),
    ]


def found(engine, filters):
    return {r.x_id for r in search(engine, "Saka", "fulltext", filters=filters).results}


def test_sources_only_search_returns_exactly_the_marked_posts(eval_db):
    flagged = source_flags(mixed_corpus(), ["listed"])
    assert {p.x_id for p in flagged if p.is_source} == {1, 3, 4}

    engine = load_eval_corpus(eval_db, flagged, ["listed"])

    assert found(engine, SearchFilters(sources_only=True)) == {1, 3, 4}
    assert found(engine, SearchFilters()) == {1, 2, 3, 4, 5, 6}


def test_every_post_is_a_source_without_a_list_snapshot(eval_db):
    flagged = source_flags(mixed_corpus(), None)
    assert all(p.is_source for p in flagged)
    engine = load_eval_corpus(eval_db, flagged, None)
    assert found(engine, SearchFilters(sources_only=True)) == {1, 2, 3, 4, 5, 6}


def test_the_public_list_membership_never_leaks_into_a_run(eval_db):
    from sqlmodel import Session

    from app.tweets.models import ListMembership

    with Session(eval_db) as session:
        session.add(
            ListMembership(list_id=1, source="x", fetched_at=WHEN, handles=["somebody-else"])
        )
        session.commit()
    flagged = source_flags(mixed_corpus(), None)
    engine = load_eval_corpus(eval_db, flagged, None)
    assert found(engine, SearchFilters(sources_only=True)) == {1, 2, 3, 4, 5, 6}


def test_load_refuses_a_corpus_whose_flags_disagree_with_the_sql_rule(eval_db):
    flagged = source_flags(mixed_corpus(), ["listed"])
    flipped = [
        p.model_copy(update={"is_source": not p.is_source}) if p.x_id == 2 else p for p in flagged
    ]
    with pytest.raises(EvaluationError, match="2"):
        load_eval_corpus(eval_db, flipped, ["listed"])


def test_flags_are_not_checked_for_a_corpus_without_them(eval_db):
    engine = load_eval_corpus(eval_db, mixed_corpus(), None)
    assert found(engine, SearchFilters()) == {1, 2, 3, 4, 5, 6}
