import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlmodel import Session, select

from app.corroboration.evaluation.scratch import (
    DEADLINE_LEAD,
    ScratchError,
    build_costs,
    derive_deadlines,
    extract_window,
    guard_scratch_url,
    seed_scratch,
    window_for,
)
from app.extraction.evaluation.dataset import DEFAULT_SET_DIR, load_context
from app.extraction.models import Extraction
from app.extraction.schemas import ExtractionOutput
from app.extraction.service import ExtractionRuntime
from app.llm.chat import ChatModelSpec
from app.llm.pricing import Price
from app.retrieval.evaluation.dataset import CorpusPost
from app.retrieval.indexing import IndexingRuntime, index_missing
from app.retrieval.tracing import NULL_TRACER
from app.tweets.models import Tweet
from tests.extraction.fakes import FakeChatModel
from tests.retrieval.fakes import FakeEmbedder
from tests.retrieval.helpers import PRICES, FixedClock

HANDLES = ["reporter"]


def _context():
    return load_context(DEFAULT_SET_DIR)


def _post(n, created_at, text_="Saka is fit"):
    return CorpusPost(
        x_id=1000 + n,
        author_handle="reporter",
        text=text_,
        created_at=created_at,
        is_repost=False,
        is_reply=False,
    )


T0 = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def _seed(db, corpus):
    players, teams, fixtures = _context()
    return seed_scratch(db, corpus, HANDLES, players, teams, fixtures)


def test_refuses_port_5432():
    with pytest.raises(ScratchError, match="5432"):
        guard_scratch_url("postgresql+psycopg://u:p@localhost:5432/eval", None)
    with pytest.raises(ScratchError, match="5432"):
        guard_scratch_url("postgresql+psycopg://u:p@localhost/eval", None)


def test_refuses_the_configured_database_url():
    configured = "postgresql+psycopg://presser:secret@localhost:55433/presser"
    with pytest.raises(ScratchError, match="configured"):
        guard_scratch_url("postgresql+psycopg://other:pw@localhost:55433/presser", configured)
    guard_scratch_url("postgresql+psycopg://eval:eval@localhost:55433/eval", configured)


def test_deadlines_are_first_kickoff_minus_90_minutes():
    _, _, fixtures = _context()
    deadlines = derive_deadlines(fixtures)
    assert DEADLINE_LEAD == timedelta(minutes=90)
    assert deadlines[6] == datetime(2026, 10, 10, 10, 0, tzinfo=UTC)
    assert deadlines[7] == datetime(2026, 10, 17, 10, 0, tzinfo=UTC)


def test_seed_loads_corpus_players_fixtures_and_gameweeks(db):
    corpus = [_post(1, T0), _post(2, T0 + timedelta(hours=1))]
    assert _seed(db, corpus) is True
    with Session(db) as session:
        assert len(session.exec(select(Tweet)).all()) == 2
        counts = {
            table: session.execute(text(f"SELECT count(*) FROM {table}")).scalar()
            for table in ("season", "team", "player", "gameweek", "fixture", "list_membership")
        }
    assert counts["season"] == 1 and counts["team"] == 20
    assert counts["player"] > 600 and counts["fixture"] == 380 and counts["list_membership"] == 1
    assert counts["gameweek"] >= 8


def test_seed_refuses_a_database_with_other_posts(db):
    with Session(db) as session:
        session.add(
            Tweet(
                x_id=1,
                author_handle="x",
                text="t",
                created_at=T0,
                first_fetched_at=T0,
                source="list",
                is_repost=False,
                is_reply=False,
                raw={},
            )
        )
        session.commit()
    with pytest.raises(ScratchError, match="other posts"):
        _seed(db, [_post(1, T0)])


def test_windows_from_the_seeded_calendar(db):
    _seed(db, [_post(1, T0)])
    with Session(db) as session:
        gw6 = window_for(session, 6)
        gw7 = window_for(session, 7)
    assert (gw6.start, gw6.end) == (
        datetime(2026, 10, 3, 10, 0, tzinfo=UTC),
        datetime(2026, 10, 10, 10, 0, tzinfo=UTC),
    )
    assert (gw7.start, gw7.end) == (
        datetime(2026, 10, 12, 21, 0, tzinfo=UTC),
        datetime(2026, 10, 17, 10, 0, tzinfo=UTC),
    )


def _runtime(count, price=100_000.0):
    fake = FakeChatModel(responses=[ExtractionOutput(events=[]) for _ in range(count)])
    spec = ChatModelSpec(provider="fake", model="fake-model", chat_model=fake)
    return ExtractionRuntime(
        provider="fake",
        model="fake-model",
        make_spec=lambda: spec,
        tracing=None,
        prices={"fake-model": Price(input_per_million=price, output_per_million=0.0, checked="x")},
        aliases=([], []),
    )


def _extracted(db):
    with Session(db) as session:
        return sorted(
            x
            for x in session.exec(
                select(Extraction.tweet_x_id).where(Extraction.status == "extracted")
            )
        )


def test_extraction_stays_in_the_window_and_stops_at_the_cap(db):
    corpus = [_post(n, T0 + timedelta(hours=n)) for n in range(1, 6)]
    corpus.append(_post(9, T0 - timedelta(days=3)))  # before the window
    corpus.append(_post(10, T0 + timedelta(days=3)))  # after the window
    _seed(db, corpus)
    summary = extract_window(
        db,
        _runtime(10),
        T0,
        T0 + timedelta(days=1),
        max_cost_usd=2.5,
        clock=FixedClock(),
        stop=threading.Event(),
    )
    # newest first: posts 5, 4, 3 are extracted at $1 a call, then the cap (3.0 >= 2.5) stops it
    assert _extracted(db) == [1003, 1004, 1005]
    assert summary.extracted == 3 and summary.stopped_by_cap is True
    assert 1009 not in _extracted(db) and 1010 not in _extracted(db)


def test_rerun_resumes_without_reseeding_or_reextracting(db):
    corpus = [_post(n, T0 + timedelta(hours=n)) for n in range(1, 4)]
    assert _seed(db, corpus) is True
    assert _seed(db, corpus) is False
    args = dict(clock=FixedClock(), stop=threading.Event())
    first = extract_window(db, _runtime(3), T0, T0 + timedelta(days=1), max_cost_usd=100, **args)
    second = extract_window(db, _runtime(3), T0, T0 + timedelta(days=1), max_cost_usd=100, **args)
    assert first.extracted == 3 and second.extracted == 0 and second.stopped_by_cap is False
    assert _extracted(db) == [1001, 1002, 1003]


def test_build_costs_sum_the_stored_costs(db):
    _seed(db, [_post(1, T0), _post(2, T0 + timedelta(hours=1))])
    embedder = FakeEmbedder()
    runtime = IndexingRuntime(
        model=embedder.model,
        make_embedder=lambda: embedder,
        prices=PRICES,
        tracing=None,
        clock=FixedClock(),
    )
    index_missing(db, runtime, embedder, NULL_TRACER, FixedClock(), threading.Event())
    extract_window(
        db,
        _runtime(2, price=100_000.0),
        T0 - timedelta(days=1),
        T0 + timedelta(days=1),
        max_cost_usd=100,
        clock=FixedClock(),
        stop=threading.Event(),
    )
    embeddings, extraction = build_costs(db)
    assert embeddings == pytest.approx(2 * 5 * 0.02 / 1_000_000)
    assert extraction == pytest.approx(2.0)


def test_run_build_scratch_seeds_embeds_and_extracts_the_windows_then_resumes(db):
    from app.corroboration.evaluation.scratch import run_build_scratch

    inside = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)  # in the GW6 window
    outside = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    corpus = [_post(1, inside), _post(2, inside + timedelta(hours=1)), _post(3, outside)]
    players, teams, fixtures = _context()
    embedder = FakeEmbedder()
    indexing = IndexingRuntime(
        model=embedder.model,
        make_embedder=lambda: embedder,
        prices=PRICES,
        tracing=None,
        clock=FixedClock(),
    )

    def run():
        return run_build_scratch(
            db,
            corpus,
            HANDLES,
            players,
            teams,
            fixtures,
            embedder=embedder,
            indexing=indexing,
            extraction=_runtime(5, price=1.0),
            gameweeks=[6, 7],
            max_cost_usd=10,
            clock=FixedClock(),
            stop=threading.Event(),
        )

    first = run()
    assert first.seeded and first.embedded == 3 and first.extract.extracted == 2
    assert [w.gameweek for w in first.windows] == [6, 7]
    second = run()
    assert not second.seeded and second.embedded == 0 and second.extract.extracted == 0
    assert _extracted(db) == [1001, 1002]
