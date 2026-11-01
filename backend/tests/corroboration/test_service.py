from datetime import timedelta

import pytest
from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from app.corroboration.judge import JudgeOutput, build_judge
from app.corroboration.schemas import PlayerRef
from app.corroboration.service import CorroborationRuntime, corroborate
from app.corroboration.sources import claimed_post_ids
from app.corroboration.tracing import LangfuseCorroborationTracer
from app.llm.pricing import Price
from app.llm.structured import StructuredCaller
from app.retrieval.search import SearchError
from app.retrieval.store import save_embedded
from tests.corroboration.fakes import FakeLangfuseClient, RecordingCorroborationTracer
from tests.corroboration.helpers import (
    ISAK,
    NOW,
    SAKA,
    SEASON,
    add_claim,
    add_fixture,
    add_flag,
    seed_reference,
)
from tests.extraction.fakes import FakeChatModel
from tests.retrieval.fakes import FakeEmbedder, SlowEmbedder
from tests.retrieval.helpers import MODEL, PRICES, FixedClock, add_tweet
from tests.tweets.membership_helpers import set_members

DEADLINE = NOW - timedelta(days=3)
SAKA_REF = PlayerRef(SEASON, SAKA, "Saka", "Arsenal")
JUDGE_MODEL = "fake/model"
JUDGE_PRICES = {
    JUDGE_MODEL: Price(input_per_million=1.0, output_per_million=2.0, checked="x"),
    **PRICES,
}


def _judge(*labels):
    responses = [
        value if isinstance(value, BaseException) else JudgeOutput(label=value) for value in labels
    ]
    fake = FakeChatModel(responses=responses, response_model=JUDGE_MODEL)
    caller = StructuredCaller(fake, JUDGE_MODEL, JUDGE_PRICES, FixedClock())
    return build_judge(caller), fake


def _runtime(judge=None, embedder=None, **kwargs) -> CorroborationRuntime:
    return CorroborationRuntime(
        embedder=embedder if embedder is not None else FakeEmbedder(),
        judge=judge,
        prices=JUDGE_PRICES,
        **kwargs,
    )


def _run(db, runtime, as_of=NOW, **kwargs):
    return corroborate(db, SAKA_REF, as_of, runtime=runtime, **kwargs)


def _claims(db) -> None:
    seed_reference(db, {6: DEADLINE})
    add_claim(db, 1, SAKA, "out", "confirmed", created_at=NOW - timedelta(hours=5), author="a1")
    add_claim(db, 2, SAKA, "out", "likely", created_at=NOW - timedelta(hours=3), author="a2")


def test_no_claim_makes_no_judge_call(db):
    seed_reference(db, {6: DEADLINE})
    add_claim(db, 1, ISAK, "out", mention="Isak")
    add_tweet(
        db, 2, "Saka news that only retrieval would find", created_at=NOW - timedelta(hours=1)
    )
    judge, fake = _judge("supports")
    embedder = FakeEmbedder()
    result = _run(db, _runtime(judge, embedder))
    assert result.anchor is None
    assert result.grade is None
    assert result.window_start == DEADLINE
    assert fake.received_messages == []
    assert embedder.calls == []
    assert result.retrieval.status == "skipped"


def test_anchor_is_newest_claim_and_a_tie_goes_to_the_higher_x_id(db):
    seed_reference(db, {6: DEADLINE})
    same = NOW - timedelta(hours=2)
    add_claim(db, 1, SAKA, "out", created_at=NOW - timedelta(hours=4))
    add_claim(db, 7, SAKA, "doubt", created_at=same)
    add_claim(db, 9, SAKA, "out", created_at=same)
    result = _run(db, _runtime())
    assert result.anchor.post.x_id == 9
    assert result.anchor.event_type == "out"


def test_sql_claims_are_labelled_against_the_anchor(db):
    seed_reference(db, {6: DEADLINE})
    add_claim(db, 1, SAKA, "confirmed_starter", created_at=NOW - timedelta(hours=6), author="a1")
    add_claim(db, 2, SAKA, "doubt", created_at=NOW - timedelta(hours=5), author="a2")
    add_claim(db, 3, SAKA, "out", created_at=NOW - timedelta(hours=4), author="a3")
    add_claim(db, 4, SAKA, "out", created_at=NOW - timedelta(hours=3), author="a4")
    result = _run(db, _runtime())
    assert result.anchor.post.x_id == 4
    assert [c.x_id for c in result.supporting] == [3]
    assert [c.x_id for c in result.contradicting] == [1]
    assert [c.x_id for c in result.related] == [2]
    assert result.reversal is False


def test_judged_posts_labelled_and_unrelated_dropped(db):
    _claims(db)
    add_tweet(db, 10, "Saka withdrawn injury", created_at=NOW - timedelta(hours=1), author="j1")
    add_tweet(
        db, 11, "Saka something else entirely", created_at=NOW - timedelta(hours=2), author="j2"
    )
    add_tweet(db, 12, "Saka is fit and starts", created_at=NOW - timedelta(minutes=30), author="j3")
    # candidates arrive newest first: 12, 10, 11
    judge, fake = _judge("contradicts", "supports", "unrelated")
    result = _run(db, _runtime(judge))
    assert len(fake.received_messages) == 3
    assert [c.x_id for c in result.supporting] == [1]
    assert [c.x_id for c in result.contradicting] == [12]
    cited = result.supporting + result.contradicting + result.related
    assert all(c.x_id not in (10, 11) for c in cited)
    assert result.retrieval.judged == 3 and result.retrieval.unjudged == 0
    assert result.newer_contradiction is True
    origins = {c.x_id: c.origin for c in result.supporting + result.contradicting}
    assert origins == {1: "sql", 12: "judge"}


def test_replay_ignores_posts_after_as_of(db):
    _claims(db)
    add_claim(db, 20, SAKA, "confirmed_starter", created_at=NOW + timedelta(hours=1), author="late")
    add_tweet(db, 21, "Saka after as of", created_at=NOW + timedelta(hours=2), author="late2")
    judge, fake = _judge("supports")
    early = _run(db, _runtime(judge), as_of=NOW)
    assert early.anchor.post.x_id == 2
    assert early.contradicting == []
    assert fake.received_messages == []
    later = _run(db, _runtime(), as_of=NOW + timedelta(hours=3))
    assert later.anchor.post.x_id == 20


def test_citations_carry_fields(db):
    _claims(db)
    add_claim(
        db,
        3,
        SAKA,
        "out",
        "rumour",
        created_at=NOW - timedelta(hours=4),
        author="lister",
        is_repost=True,
        reposted_author_handle="Origin",
    )
    add_tweet(db, 10, "Saka is definitely out", created_at=NOW - timedelta(minutes=10), author="j1")
    judge, _ = _judge("contradicts")
    new_since = NOW - timedelta(hours=4, minutes=30)
    result = _run(db, _runtime(judge), new_since=new_since)

    by_id = {c.x_id: c for c in result.supporting}
    assert set(by_id) == {3, 1}
    repost = by_id[3]
    assert repost.url == "https://x.com/lister/status/3"
    assert (repost.author_handle, repost.reposted_author_handle) == ("lister", "Origin")
    assert repost.created_at == NOW - timedelta(hours=4)
    assert (repost.certainty, repost.origin, repost.label) == ("rumour", "sql", "supports")
    assert repost.freshness == "new"
    judged = {c.x_id: c for c in result.contradicting}[10]
    assert (judged.certainty, judged.origin, judged.freshness) == (None, "judge", "new")
    assert by_id[1].freshness == "context"


def test_new_since_defaults_to_the_window_start(db):
    _claims(db)
    result = _run(db, _runtime())
    assert result.new_since == DEADLINE
    assert {c.freshness for c in result.supporting} == {"new"}


def test_the_anchor_own_account_is_not_a_confirmation(db):
    seed_reference(db, {6: DEADLINE})
    add_claim(db, 1, SAKA, "out", created_at=NOW - timedelta(hours=5), author="same")
    add_claim(db, 2, SAKA, "out", created_at=NOW - timedelta(hours=1), author="Same")
    result = _run(db, _runtime())
    assert result.supporting == []
    assert result.grade.level == "medium"


def _embed_one(db, x_id: int) -> None:
    with Session(db) as session, session.begin():
        save_embedded(
            session,
            x_id=x_id,
            model=MODEL,
            vector=[1.0, 0.0, 0.0],
            input_tokens=5,
            cost_usd=None,
            latency_seconds=None,
            attempts=1,
            now=NOW,
        )


def test_slow_embedding_falls_back_to_fulltext(db):
    _claims(db)
    add_tweet(db, 10, "Saka withdrawn injury", created_at=NOW - timedelta(hours=1), author="j1")
    _embed_one(db, 10)
    judge, fake = _judge("supports")
    slow = SlowEmbedder(delay_seconds=6.0)
    result = _run(db, _runtime(judge, slow))
    assert slow.timeouts == [5.0]
    assert result.retrieval.failed_legs == ("vector",)
    assert "TimeoutError" in result.retrieval.failure
    assert result.retrieval.judged == 1
    assert [c.x_id for c in result.supporting] == [1]


def test_without_key_sql_only(db):
    _claims(db)
    add_tweet(db, 10, "Saka withdrawn injury", created_at=NOW - timedelta(hours=1))
    runtime = CorroborationRuntime(
        embedder=None, judge=None, skipped_reason="OPENROUTER_API_KEY is not set"
    )
    result = _run(db, runtime)
    assert result.retrieval.status == "skipped"
    assert result.retrieval.skipped_reason == "OPENROUTER_API_KEY is not set"
    assert [c.x_id for c in result.supporting] == [1]
    assert result.grade is not None


@pytest.mark.parametrize(
    "error", [SearchError("search down"), OperationalError("select", {}, Exception("db down"))]
)
def test_a_failed_retrieval_leaves_the_sql_only_result(db, monkeypatch, error):
    _claims(db)
    add_tweet(db, 10, "Saka withdrawn injury", created_at=NOW - timedelta(hours=1), author="j1")

    def failing(*args, **kwargs):
        raise error

    monkeypatch.setattr("app.corroboration.service.retrieval_candidates", failing)
    judge, fake = _judge("supports")
    result = _run(db, _runtime(judge))
    assert result.retrieval.status == "ran"
    assert result.retrieval.failure == f"retrieval failed: {type(error).__name__}"
    assert (result.retrieval.judged, result.retrieval.unjudged) == (0, 0)
    assert fake.received_messages == []
    assert result.anchor is not None and result.anchor.post.x_id == 2
    assert [c.x_id for c in result.supporting] == [1]
    assert result.grade is not None


def test_failed_judge_call_counts_unjudged(db):
    _claims(db)
    add_tweet(db, 10, "Saka a", created_at=NOW - timedelta(hours=1), author="j1")
    add_tweet(db, 11, "Saka b", created_at=NOW - timedelta(hours=2), author="j2")
    add_tweet(db, 12, "Saka c", created_at=NOW - timedelta(hours=3), author="j3")
    boom = RuntimeError("down")
    # candidates arrive newest first: 10, 11, 12; the middle one fails on all three attempts
    judge, _ = _judge("supports", boom, boom, boom, "supports")
    tracer = RecordingCorroborationTracer()
    result = _run(db, _runtime(judge, tracer=tracer))
    assert result.retrieval.judged == 2
    assert result.retrieval.unjudged == 1
    assert [c.x_id for c in result.supporting] == [1]
    errors = [g for g in tracer.generations if g.get("error_class")]
    assert [g["error_class"] for g in errors] == ["RuntimeError"]


def test_trace_holds_every_step(db):
    _claims(db)
    add_tweet(db, 10, "Saka withdrawn injury", created_at=NOW - timedelta(hours=1), author="j1")
    add_tweet(db, 11, "Saka rumour", created_at=NOW - timedelta(hours=2), author="j2")
    judge, _ = _judge("supports", "related")
    client = FakeLangfuseClient()
    tracer = LangfuseCorroborationTracer(client)
    result = _run(db, _runtime(judge, tracer=tracer))

    (root,) = client.named("corroboration")
    assert root["input"]["as_of"] == NOW.isoformat()
    assert root["input"]["player"]["name"] == "Saka"
    assert root["input"]["new_since"] == DEADLINE.isoformat()
    (update,) = root["updates"]
    assert update["output"]["grade"] == result.grade.level
    assert update["output"]["anchor"]["x_id"] == 2
    assert result.trace_id == "trace-1"
    (search,) = client.named("retrieval-search")
    assert search["parent"] == "corroboration"
    generations = client.named("corroboration-judge")
    assert len(generations) == 2
    assert all(g["parent"] == "corroboration" for g in generations)
    assert all(g["model"] == JUDGE_MODEL and g["cost_details"]["total"] > 0 for g in generations)
    assert all(g["usage_details"] == {"input": 10, "output": 5} for g in generations)


def test_a_tracing_failure_does_not_fail_the_corroboration(db):
    _claims(db)
    judge, _ = _judge()
    tracer = LangfuseCorroborationTracer(FakeLangfuseClient(fail=True))
    result = _run(db, _runtime(judge, tracer=tracer))
    assert result.anchor is not None
    assert result.trace_id is None


@pytest.mark.parametrize("certainty", ["confirmed", "likely", "rumour"])
def test_the_grade_follows_the_anchor_certainty(db, certainty):
    seed_reference(db, {6: DEADLINE})
    add_claim(db, 1, SAKA, "out", certainty, created_at=NOW - timedelta(hours=1))
    result = _run(db, _runtime())
    assert (
        result.grade.level
        == {"confirmed": "medium", "likely": "medium", "rumour": "low"}[certainty]
    )


def test_given_anchor_is_the_anchor(db):
    seed_reference(db, {6: DEADLINE})
    add_claim(db, 1, SAKA, "out", "likely", created_at=NOW - timedelta(hours=5), author="a1")
    add_claim(db, 2, SAKA, "doubt", "likely", created_at=NOW - timedelta(hours=4), author="a2")
    add_claim(db, 3, SAKA, "out", "rumour", created_at=NOW - timedelta(hours=3), author="a3")

    result = _run(db, _runtime(), anchor_x_id=1)

    assert result.anchor is not None and result.anchor.post.x_id == 1
    assert result.anchor.certainty == "likely"
    assert [c.x_id for c in result.supporting] == [3]
    assert [c.x_id for c in result.related] == [2]

    newest = _run(db, _runtime(), anchor_x_id=999)
    assert newest.anchor is not None and newest.anchor.post.x_id == 3
    assert _run(db, _runtime()).anchor.post.x_id == 3


def test_quote_and_quoted_leak_give_one_supporting_account(db):
    seed_reference(db, {6: DEADLINE})
    add_claim(db, 1, SAKA, "out", author="outsider", created_at=NOW - timedelta(hours=5))
    add_claim(
        db, 2, SAKA, "out", author="member_a", quoted_x_id=1, created_at=NOW - timedelta(hours=4)
    )
    add_claim(db, 3, SAKA, "out", author="member_b", created_at=NOW - timedelta(hours=1))
    set_members(db, ["member_a", "member_b"])

    result = _run(db, _runtime())

    assert result.anchor.post.x_id == 3
    assert [c.x_id for c in result.supporting] == [2]
    assert result.grade.level == "medium"


def test_contradicting_quote_gives_one_contradicting_account(db):
    seed_reference(db, {6: DEADLINE})
    add_claim(db, 1, SAKA, "out", author="outsider", created_at=NOW - timedelta(hours=5))
    add_claim(
        db,
        2,
        SAKA,
        "confirmed_starter",
        author="member_a",
        quoted_x_id=1,
        created_at=NOW - timedelta(hours=4),
    )
    add_claim(db, 3, SAKA, "out", author="member_b", created_at=NOW - timedelta(hours=1))
    set_members(db, ["member_a", "member_b"])

    result = _run(db, _runtime())

    assert [c.x_id for c in result.contradicting] == [2]
    assert [c.x_id for c in result.supporting] == [1]


def test_quoting_anchor_counts_as_the_quoted_account_in_counts_and_trace(db):
    seed_reference(db, {6: DEADLINE})
    add_claim(db, 1, SAKA, "out", author="outsider", created_at=NOW - timedelta(hours=5))
    add_claim(db, 2, SAKA, "out", author="member_b", created_at=NOW - timedelta(hours=4))
    add_claim(db, 3, SAKA, "out", author="member_a", created_at=NOW - timedelta(hours=3))
    add_claim(
        db, 4, SAKA, "out", author="member_a", quoted_x_id=1, created_at=NOW - timedelta(hours=1)
    )
    set_members(db, ["member_a", "member_b"])
    client = FakeLangfuseClient()

    result = _run(db, _runtime(tracer=LangfuseCorroborationTracer(client)))

    assert result.anchor.post.x_id == 4
    assert [c.x_id for c in result.supporting] == [2]
    (root,) = client.named("corroboration")
    (update,) = root["updates"]
    assert update["output"]["anchor"]["account"] == "outsider"


def test_an_absence_for_another_match_is_neither_a_claim_nor_judged(db):
    _claims(db)
    add_claim(
        db,
        3,
        SAKA,
        "out",
        "confirmed",
        created_at=NOW - timedelta(hours=1),
        author="a3",
        text="Saka rested for the cup",
        for_next_pl_match=False,
    )
    judge, fake = _judge()
    result = _run(db, _runtime(judge))
    assert result.anchor.post.x_id == 2
    cited = result.supporting + result.contradicting + result.related
    assert all(c.x_id != 3 for c in cited)
    assert fake.received_messages == []
    with Session(db) as session:
        assert 3 in claimed_post_ids(session, SAKA_REF, DEADLINE, NOW)


def test_a_stale_lineup_is_neither_a_claim_nor_judged(db):
    _claims(db)
    add_fixture(db, 1, 6, NOW + timedelta(days=4))
    add_claim(
        db,
        3,
        SAKA,
        "benched",
        "confirmed",
        created_at=NOW - timedelta(hours=1),
        author="a3",
        text="Saka on the bench tonight",
    )
    judge, fake = _judge()
    result = _run(db, _runtime(judge))
    assert result.anchor.post.x_id == 2
    cited = result.supporting + result.contradicting + result.related
    assert all(c.x_id != 3 for c in cited)
    assert fake.received_messages == []


def _available_world(db):
    seed_reference(db, {6: DEADLINE})


def test_available_anchor_by_reversed_news(db):
    _available_world(db)
    add_claim(db, 1, SAKA, "out", "confirmed", created_at=NOW - timedelta(hours=5), author="a1")
    add_claim(db, 2, SAKA, "out", "likely", created_at=NOW - timedelta(hours=4), author="a2")
    add_claim(
        db, 3, SAKA, "available", "confirmed", created_at=NOW - timedelta(hours=1), author="a3"
    )
    result = _run(db, _runtime())
    assert result.anchor.post.x_id == 3
    assert result.anchor.event_type == "available"
    assert [c.x_id for c in result.contradicting] == [2, 1]
    assert result.reversal is True
    assert result.reversed_post.x_id == 2
    assert result.reversed_flag is None


def test_available_anchor_by_a_non_a_flag_without_news(db):
    _available_world(db)
    add_flag(db, SAKA, "a", "", NOW - timedelta(days=20))
    add_flag(db, SAKA, "d", "Hip injury - 75% chance of playing", NOW - timedelta(days=2))
    add_claim(
        db, 3, SAKA, "available", "confirmed", created_at=NOW - timedelta(hours=1), author="a3"
    )
    result = _run(db, _runtime())
    assert result.anchor.post.x_id == 3
    assert result.reversed_post is None
    assert result.reversed_flag.news == "Hip injury - 75% chance of playing"
    assert result.reversal is True
    assert result.contradicting == []


def test_available_with_an_a_flag_and_no_news_has_no_card(db):
    _available_world(db)
    add_flag(db, SAKA, "a", "", NOW - timedelta(days=5))
    add_claim(
        db, 3, SAKA, "available", "confirmed", created_at=NOW - timedelta(hours=1), author="a3"
    )
    judge, fake = _judge("contradicts")
    result = _run(db, _runtime(judge))
    assert result.anchor is None
    assert fake.received_messages == []


def test_a_flag_turned_a_before_the_post_gives_no_card(db):
    _available_world(db)
    add_flag(db, SAKA, "d", "Knock", NOW - timedelta(days=5))
    add_flag(db, SAKA, "a", "", NOW - timedelta(hours=3))
    add_claim(
        db, 3, SAKA, "available", "confirmed", created_at=NOW - timedelta(hours=1), author="a3"
    )
    assert _run(db, _runtime()).anchor is None


def test_an_available_chain_after_out_with_an_a_flag_has_no_card(db):
    _available_world(db)
    add_flag(db, SAKA, "a", "", NOW - timedelta(hours=2))
    add_claim(db, 1, SAKA, "out", "likely", created_at=NOW - timedelta(hours=6), author="a1")
    add_claim(db, 2, SAKA, "available", "likely", created_at=NOW - timedelta(hours=4), author="a2")
    add_claim(db, 3, SAKA, "available", "likely", created_at=NOW - timedelta(hours=1), author="a3")
    judge, fake = _judge("contradicts")
    result = _run(db, _runtime(judge))
    assert result.anchor is None
    assert result.retrieval.status == "skipped"
    assert fake.received_messages == []


def test_reversal_judged_older_contradiction_alone_is_none(db):
    _available_world(db)
    add_claim(
        db, 3, SAKA, "available", "confirmed", created_at=NOW - timedelta(hours=1), author="a3"
    )
    add_tweet(db, 10, "Saka is ruled out", created_at=NOW - timedelta(hours=3), author="j1")
    judge, fake = _judge("contradicts")
    assert _run(db, _runtime(judge)).anchor is None
    assert fake.received_messages == []


def test_reversal_judged_older_contradiction_does_not_make_one(db):
    _available_world(db)
    add_claim(db, 1, SAKA, "out", "likely", created_at=NOW - timedelta(hours=1), author="a1")
    add_tweet(db, 10, "Saka looks fine", created_at=NOW - timedelta(hours=3), author="j1")
    judge, _ = _judge("contradicts")
    result = _run(db, _runtime(judge))
    assert result.reversal is False
    assert [c.x_id for c in result.contradicting] == [10]


def test_reversal_another_accounts_older_contradiction(db):
    _available_world(db)
    add_claim(db, 1, SAKA, "confirmed_starter", created_at=NOW - timedelta(hours=5), author="a1")
    add_claim(db, 2, SAKA, "out", "likely", created_at=NOW - timedelta(hours=1), author="a2")
    result = _run(db, _runtime())
    assert result.reversal is True
    assert result.reversed_post.x_id == 1
    assert [c.x_id for c in result.contradicting] == [1]


def test_reversal_newer_supporting_claim_ends_it(db):
    _available_world(db)
    add_claim(db, 1, SAKA, "confirmed_starter", created_at=NOW - timedelta(hours=5), author="a1")
    add_claim(db, 2, SAKA, "out", "likely", created_at=NOW - timedelta(hours=3), author="a2")
    add_claim(db, 3, SAKA, "out", "likely", created_at=NOW - timedelta(hours=1), author="a3")
    result = _run(db, _runtime())
    assert result.reversal is False
    assert result.reversed_post is None
    assert [c.x_id for c in result.contradicting] == [1]


def test_one_judge_pass_for_a_non_reversing_available(db):
    _available_world(db)
    add_claim(db, 1, SAKA, "confirmed_starter", created_at=NOW - timedelta(hours=5), author="a1")
    add_claim(
        db, 3, SAKA, "available", "confirmed", created_at=NOW - timedelta(hours=1), author="a3"
    )
    add_tweet(db, 10, "Saka trained all week", created_at=NOW - timedelta(hours=2), author="j1")
    judge, fake = _judge("related")
    result = _run(db, _runtime(judge))
    assert result.anchor.post.x_id == 1
    assert result.anchor.event_type == "confirmed_starter"
    assert {c.x_id for c in result.related} == {3}
    assert len(fake.received_messages) == 1
    assert "Event type: confirmed_starter" in fake.received_messages[0][1].content
    assert result.retrieval.judged == 1


def test_only_available_claims_give_no_anchor(db):
    _available_world(db)
    add_claim(
        db, 3, SAKA, "available", "confirmed", created_at=NOW - timedelta(hours=1), author="a3"
    )
    _embed_one(db, 3)
    judge, fake = _judge("unrelated")
    result = _run(db, _runtime(judge))
    assert result.anchor is None
    assert result.grade is None
    assert result.retrieval.status == "skipped"
    assert "reverse" in result.retrieval.skipped_reason


def test_reversal_own_account_changes_story(db):
    _available_world(db)
    add_claim(db, 1, SAKA, "out", "likely", created_at=NOW - timedelta(hours=5), author="reporter")
    add_claim(
        db,
        2,
        SAKA,
        "available",
        "confirmed",
        created_at=NOW - timedelta(hours=1),
        author="Reporter",
    )
    result = _run(db, _runtime())
    assert result.anchor.post.x_id == 2
    assert result.reversal is True
    assert result.reversed_post.x_id == 1
    assert result.contradicting == []


def test_given_available_anchor_that_does_not_reverse_is_replaced(db):
    _available_world(db)
    add_claim(db, 1, SAKA, "confirmed_starter", created_at=NOW - timedelta(hours=5), author="a1")
    add_claim(
        db, 3, SAKA, "available", "confirmed", created_at=NOW - timedelta(hours=1), author="a3"
    )
    result = _run(db, _runtime(), anchor_x_id=3)
    assert result.anchor.post.x_id == 1


def test_a_starter_leak_after_the_out_keeps_the_card_on_the_starter(db):
    _available_world(db)
    add_claim(db, 1, SAKA, "out", "likely", created_at=NOW - timedelta(hours=6), author="a1")
    add_claim(db, 2, SAKA, "confirmed_starter", created_at=NOW - timedelta(hours=4), author="a2")
    add_claim(
        db, 3, SAKA, "available", "confirmed", created_at=NOW - timedelta(hours=1), author="a3"
    )
    result = _run(db, _runtime())
    assert result.anchor.post.x_id == 2
    assert result.anchor.event_type == "confirmed_starter"
    assert [c.x_id for c in result.contradicting] == [1]
    assert [c.x_id for c in result.related] == [3]


def test_an_out_newer_than_the_available_claim_reverses_it(db):
    _available_world(db)
    add_claim(
        db, 1, SAKA, "available", "confirmed", created_at=NOW - timedelta(hours=5), author="a1"
    )
    add_claim(db, 2, SAKA, "out", "likely", created_at=NOW - timedelta(hours=1), author="a2")
    result = _run(db, _runtime())
    assert result.anchor.post.x_id == 2
    assert result.anchor.event_type == "out"
    assert [c.x_id for c in result.contradicting] == [1]
    assert result.reversed_post.x_id == 1


def test_a_judged_out_newer_than_the_available_claim_needs_the_flag(db):
    _available_world(db)
    add_claim(
        db, 3, SAKA, "available", "confirmed", created_at=NOW - timedelta(hours=3), author="a3"
    )
    add_tweet(db, 10, "Saka is ruled out", created_at=NOW - timedelta(hours=1), author="j1")
    _embed_one(db, 10)
    judge, fake = _judge("contradicts")
    result = _run(db, _runtime(judge))
    assert fake.received_messages == []
    assert result.anchor is None
    assert result.retrieval.status == "skipped"

    add_flag(db, SAKA, "d", "Knock", NOW - timedelta(hours=5))
    judge, fake = _judge("contradicts")
    flagged = _run(db, _runtime(judge))
    assert len(fake.received_messages) == 1
    assert flagged.anchor.post.x_id == 3
    assert flagged.newer_contradiction is True


def test_a_given_older_available_anchor_with_a_newer_out_gives_no_anchor(db):
    _available_world(db)
    add_claim(
        db, 1, SAKA, "available", "confirmed", created_at=NOW - timedelta(hours=5), author="a1"
    )
    add_claim(db, 2, SAKA, "out", "likely", created_at=NOW - timedelta(hours=1), author="a2")
    assert _run(db, _runtime(), anchor_x_id=1).anchor is None


def test_no_anchor_runs_no_retrieval(db, monkeypatch):
    _available_world(db)
    add_claim(
        db, 3, SAKA, "available", "confirmed", created_at=NOW - timedelta(hours=1), author="a3"
    )

    def failing(*args, **kwargs):
        raise AssertionError("retrieval must not run without an anchor")

    monkeypatch.setattr("app.corroboration.service.retrieval_candidates", failing)
    judge, fake = _judge("contradicts")
    result = _run(db, _runtime(judge))
    assert result.anchor is None
    assert result.retrieval.status == "skipped"
    assert result.retrieval.failure is None
    assert fake.received_messages == []


def test_a_judged_support_adds_no_account(db):
    _claims(db)
    add_tweet(db, 10, "Saka tips: too early to say", created_at=NOW - timedelta(hours=1))
    judge, fake = _judge("supports")
    with_judge = _run(db, _runtime(judge))
    sql_only = _run(db, CorroborationRuntime(embedder=None, judge=None))
    assert len(fake.received_messages) == 1
    assert [c.x_id for c in with_judge.supporting] == [1]
    assert all(c.origin == "sql" for c in with_judge.related)
    assert with_judge.retrieval.judged == 1
    assert with_judge.grade.level == sql_only.grade.level


def test_a_judged_contradiction_still_lowers_the_grade(db):
    _claims(db)
    add_tweet(db, 10, "Saka is fit and starts", created_at=NOW - timedelta(minutes=30))
    judge, _ = _judge("contradicts")
    with_judge = _run(db, _runtime(judge))
    sql_only = _run(db, CorroborationRuntime(embedder=None, judge=None))
    assert [c.x_id for c in with_judge.contradicting] == [10]
    assert with_judge.newer_contradiction is True
    assert (sql_only.grade.level, with_judge.grade.level) == ("medium", "low")
