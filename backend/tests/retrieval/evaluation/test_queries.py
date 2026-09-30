from datetime import timedelta

from sqlmodel import Session
from typer.testing import CliRunner

from app.extraction.schemas import LinkedEvent
from app.extraction.store import ExtractionRecord, save_extraction
from app.fpl.models.reference import Player, Season, Team
from app.llm.chat import DEFAULT_MODEL
from app.retrieval.cli import RetrievalCliDeps, app
from app.retrieval.evaluation.dataset import CorpusPost, load_queries, write_corpus
from app.retrieval.evaluation.llm import DEFAULT_LABEL_MODEL, StructuredCaller, WrittenQuery
from app.retrieval.evaluation.queries import (
    CurrentEvent,
    QueryCounts,
    build_queries,
    current_events,
    make_query_writer,
)
from tests.extraction.fakes import FakeChatModel
from tests.retrieval.helpers import NOW, FixedClock, add_tweet
from tests.retrieval.test_cli import _deps

LONG = "A reasonably long post about a player and some team news today"


def _post(x_id: int, text: str = LONG, repost: bool = False) -> CorpusPost:
    return CorpusPost(
        x_id=x_id,
        author_handle="author",
        text=f"{text} {x_id}",
        created_at=NOW - timedelta(minutes=x_id),
        is_repost=repost,
        is_reply=False,
    )


def _writer(calls: list | None = None):
    def write(post: CorpusPost, language: str) -> str:
        if calls is not None:
            calls.append((post.x_id, language))
        return f"query {post.x_id} {language}"

    return write


def _events(count: int) -> list[CurrentEvent]:
    types = ["out", "doubt", "benched", "confirmed_starter"]
    return [
        CurrentEvent(x_id=i + 1, player=f"Player{i}", event_type=types[i % 4]) for i in range(count)
    ]


def test_builder_writes_counts_by_language_and_origin():
    corpus = [_post(i) for i in range(1, 121)]
    result = build_queries(corpus, _events(30), _writer())

    by_kind: dict[tuple[str, str], int] = {}
    for query in result.queries:
        by_kind[(query.origin, query.language)] = by_kind.get((query.origin, query.language), 0) + 1
    assert by_kind == {
        ("event", "en"): 15,
        ("event", "pl"): 5,
        ("post", "en"): 15,
        ("post", "pl"): 5,
    }
    assert len(result.queries) == 40
    assert len({query.id for query in result.queries}) == 40
    post_query = next(q for q in result.queries if q.origin == "post")
    assert post_query.source_x_id is not None
    assert post_query.judgements == []
    assert next(q for q in result.queries if q.origin == "event").event is not None
    assert result.skipped_posts == 0


def test_event_queries_templated_from_current_events(db):
    with Session(db) as session:
        session.add(Season(label="2026/27"))
        session.add(Team(season="2026/27", fpl_id=1, name="Team", short_name="TM"))
        session.flush()
        session.add(
            Player(
                season="2026/27",
                fpl_id=7,
                web_name="Saka",
                first_name="Bukayo",
                second_name="Saka",
                team_fpl_id=1,
                position=3,
            )
        )
        session.commit()
    for x_id in (1, 2):
        add_tweet(db, x_id, f"Post {x_id}")

    def record(x_id, status, finished_at):
        return ExtractionRecord(
            tweet_x_id=x_id,
            status=status,
            provider="p",
            model="m",
            prompt_version="v",
            started_at=finished_at,
            finished_at=finished_at,
            attempts=1,
        )

    def event(mention, event_type, fpl_id=None):
        return LinkedEvent(
            mention=mention,
            team=None,
            player_season="2026/27" if fpl_id else None,
            player_fpl_id=fpl_id,
            event_type=event_type,
            certainty="confirmed",
        )

    with Session(db) as session:
        # the older extraction of post 1 is superseded by the newer one
        save_extraction(session, record(1, "extracted", NOW), [event("Ødegaard", "doubt")])
        save_extraction(
            session,
            record(1, "extracted", NOW + timedelta(minutes=1)),
            [event("Bukayo", "out", fpl_id=7)],
        )
        save_extraction(session, record(2, "failed", NOW), [])
        save_extraction(
            session, record(2, "extracted", NOW - timedelta(hours=1)), [event("Nobody", "benched")]
        )

    events = current_events(db, [1, 2])

    assert [(e.x_id, e.player, e.event_type) for e in events] == [
        (1, "Saka", "out"),
        (2, "Nobody", "benched"),
    ]
    result = build_queries([_post(1), _post(2)], events, _writer(), QueryCounts(2, 0, 0, 0), seed=1)
    assert sorted(q.text for q in result.queries) == ["Nobody benched", "Saka injury"]
    assert all(q.origin == "event" and q.language == "en" for q in result.queries)


def test_event_queries_in_polish_use_polish_templates():
    result = build_queries(
        [_post(i) for i in range(1, 50)], _events(8), _writer(), QueryCounts(2, 3, 0, 0)
    )
    polish = [q for q in result.queries if q.language == "pl" and q.origin == "event"]
    assert len(polish) == 3
    assert all(
        ("kontuzja" in q.text or "znakiem" in q.text or "ławce" in q.text or "składzie" in q.text)
        for q in polish
    )


def test_split_is_stratified_and_deterministic():
    corpus = [_post(i) for i in range(1, 121)]
    first = build_queries(corpus, _events(30), _writer(), seed=3)
    second = build_queries(corpus, _events(30), _writer(), seed=3)
    assert first.queries == second.queries

    for kind, dev in {
        ("event", "en"): 4,
        ("event", "pl"): 2,
        ("post", "en"): 4,
        ("post", "pl"): 2,
    }.items():
        group = [q for q in first.queries if (q.origin, q.language) == kind]
        splits = [q.split for q in group]
        assert splits.count("dev") == dev
        assert splits == ["dev"] * dev + ["test"] * (len(group) - dev)

    other = build_queries(corpus, _events(30), _writer(), seed=4)
    assert [q.source_x_id for q in other.queries] != [q.source_x_id for q in first.queries]


def test_shortfall_filled_with_post_queries():
    corpus = [_post(i) for i in range(1, 60)]
    result = build_queries(corpus, _events(3), _writer())
    kinds = [(q.origin, q.language) for q in result.queries]
    assert kinds.count(("event", "en")) == 3
    assert kinds.count(("event", "pl")) == 0
    assert kinds.count(("post", "en")) == 15 + 12
    assert kinds.count(("post", "pl")) == 5 + 5
    assert len({q.source_x_id for q in result.queries if q.origin == "post"}) == 37


def test_posts_are_not_reused_reposts_and_short_posts_excluded():
    corpus = [_post(1, repost=True), _post(2, "too short"), _post(3), _post(4)]
    calls: list = []
    build_queries(corpus, [], _writer(calls), QueryCounts(0, 0, 1, 1))
    assert sorted(x_id for x_id, _ in calls) == [3, 4]


def test_failed_post_is_skipped_and_counted():
    corpus = [_post(i) for i in range(1, 10)]
    calls: list = []

    def flaky(post: CorpusPost, language: str) -> str:
        calls.append(post.x_id)
        if len(calls) == 1:
            raise RuntimeError("model down")
        return f"query {post.x_id}"

    result = build_queries(corpus, [], flaky, QueryCounts(0, 0, 2, 0))
    assert result.skipped_posts == 1
    assert len([q for q in result.queries if q.origin == "post"]) == 2
    assert calls[0] not in {q.source_x_id for q in result.queries}


def test_label_model_default_matches_extraction_default():
    assert DEFAULT_LABEL_MODEL == DEFAULT_MODEL


def test_query_writer_uses_the_model_and_normalises_whitespace():
    fake = FakeChatModel(responses=[WrittenQuery(query="  saka   injury \n update ")])
    caller = StructuredCaller(fake, "openai/gpt-6-luna", {}, FixedClock())
    write = make_query_writer(caller)
    assert write(_post(1), "pl") == "saka injury update"
    prompt_messages = fake.received_messages[0]
    assert "Polish" in prompt_messages[1].content


def test_build_queries_command_writes_file_and_prints_counts_and_cost(db, tmp_path):
    corpus = [_post(i) for i in range(1, 30)]
    write_corpus(tmp_path / "corpus.jsonl", corpus)
    fake = FakeChatModel(responses=[WrittenQuery(query=f"query {i}") for i in range(30)])
    deps = RetrievalCliDeps(
        **{**_deps(db).__dict__, "make_chat_model": lambda model: fake, "prices": {}}
    )
    output = tmp_path / "queries.jsonl"

    result = CliRunner().invoke(
        app,
        [
            "build-queries",
            "--corpus",
            str(tmp_path / "corpus.jsonl"),
            "--output",
            str(output),
            "--post-en",
            "3",
            "--post-pl",
            "2",
            "--event-en",
            "0",
            "--event-pl",
            "0",
        ],
        obj=deps,
    )

    assert result.exit_code == 0, result.output
    assert "queries: 5" in result.stdout
    assert "  post en: 3" in result.stdout
    assert "  post pl: 2" in result.stdout
    assert "skipped posts: 0" in result.stdout
    assert "total cost: n/a" in result.stdout
    assert len(load_queries(output)) == 5

    again = CliRunner().invoke(
        app,
        ["build-queries", "--corpus", str(tmp_path / "corpus.jsonl"), "--output", str(output)],
        obj=deps,
    )
    assert again.exit_code == 1
    assert "--force" in again.stderr


def test_build_queries_without_a_key_names_the_variable(db, tmp_path):
    write_corpus(tmp_path / "corpus.jsonl", [_post(1)])
    deps = RetrievalCliDeps(**{**_deps(db).__dict__, "make_chat_model": None})
    result = CliRunner().invoke(
        app,
        [
            "build-queries",
            "--corpus",
            str(tmp_path / "corpus.jsonl"),
            "--output",
            str(tmp_path / "q.jsonl"),
        ],
        obj=deps,
    )
    assert result.exit_code == 1
    assert "OPENROUTER_API_KEY" in result.stderr
