import os
import re
from datetime import timedelta

import pytest
from typer.testing import CliRunner

from app.core.clock import Clock
from app.core.local_time import parse_local
from app.corroboration.cli import CorroborationCliDeps, app
from app.corroboration.config import CorroborationSettings
from app.corroboration.judge import JudgeOutput, build_judge
from app.corroboration.service import CorroborationRuntime
from app.corroboration.tracing import LangfuseCorroborationTracer
from app.llm.pricing import Price
from app.llm.structured import StructuredCaller
from tests.corroboration.fakes import FakeLangfuseClient
from tests.corroboration.helpers import (
    GABRIEL_JESUS,
    GABRIEL_MAGALHAES,
    ISAK,
    NOW,
    SAKA,
    add_claim,
    seed_reference,
)
from tests.extraction.fakes import FakeChatModel
from tests.retrieval.fakes import FakeEmbedder
from tests.retrieval.helpers import PRICES, FixedClock, add_tweet

VARIABLES = re.compile(r"(LLM_.*|LANGFUSE_.*|.*_API_KEY|USD_PLN_RATE|EMBEDDING_MODEL)")
DEADLINE = NOW - timedelta(days=3)
PRICES_ALL = {"fake/model": Price(1.0, 2.0, "x"), **PRICES}


@pytest.fixture(autouse=True)
def _no_provider_variables(monkeypatch):
    for name in list(os.environ):
        if VARIABLES.fullmatch(name):
            monkeypatch.delenv(name)


class RecordingRuntimeFactory:
    def __init__(self, runtime: CorroborationRuntime) -> None:
        self.runtime = runtime
        self.calls = 0

    def __call__(self) -> CorroborationRuntime:
        self.calls += 1
        return self.runtime


def _judge(*labels):
    fake = FakeChatModel(
        responses=[JudgeOutput(label=label) for label in labels], response_model="fake/model"
    )
    return build_judge(StructuredCaller(fake, "fake/model", PRICES_ALL, FixedClock()))


def _deps(db, runtime=None, clock: Clock | None = None) -> CorroborationCliDeps:
    runtime = runtime or CorroborationRuntime(embedder=FakeEmbedder(), judge=_judge("supports"))
    return CorroborationCliDeps(
        engine=db,
        settings=CorroborationSettings(_env_file=None),
        clock=clock or FixedClock(NOW),
        make_runtime=RecordingRuntimeFactory(runtime),
        aliases=([], []),
    )


def _run(deps, *args):
    return CliRunner().invoke(app, list(args), obj=deps)


def _seed_saka(db) -> None:
    seed_reference(db, {6: DEADLINE})
    add_claim(db, 1, SAKA, "out", "confirmed", created_at=NOW - timedelta(hours=6), author="a1")
    add_claim(db, 2, SAKA, "out", "likely", created_at=NOW - timedelta(hours=4), author="a2")
    add_claim(
        db,
        3,
        SAKA,
        "confirmed_starter",
        "likely",
        created_at=NOW - timedelta(hours=8),
        author="a3",
    )
    add_tweet(
        db, 10, "Saka is out for weeks", created_at=NOW - timedelta(hours=1), author="judged1"
    )


def test_prints_anchor_grade_flags_then_the_groups_in_order(db):
    _seed_saka(db)
    add_claim(db, 4, SAKA, "doubt", "rumour", created_at=NOW - timedelta(hours=5), author="a4")
    result = _run(_deps(db), "Saka", "--new-since", "2026-09-29T16:00")
    assert result.exit_code == 0, result.output
    out = result.stdout
    positions = [
        out.index("Anchor:"),
        out.index("Grade:"),
        out.index("Flags:"),
        out.index("Supporting"),
        out.index("Contradicting"),
        out.index("Related"),
    ]
    assert positions == sorted(positions)
    related = out[out.index("Related") :]
    assert "Related (1):" in related
    assert "https://x.com/a4/status/4" in related
    assert "@a2" in out
    assert "out (likely)" in out
    assert "reversal: yes" in out
    assert "https://x.com/a1/status/1" in out
    assert "https://x.com/judged1/status/10" in out


def test_supporting_posts_are_split_into_new_and_context(db):
    _seed_saka(db)
    result = _run(_deps(db), "Saka", "--new-since", "2026-09-29T14:00")
    out = result.stdout
    supporting = out[out.index("Supporting") : out.index("Contradicting")]
    assert supporting.index("new:") < supporting.index("https://x.com/judged1/status/10")
    assert supporting.index("context:") < supporting.index("https://x.com/a1/status/1")
    assert supporting.index("https://x.com/judged1/status/10") < supporting.index("context:")


def test_times_are_parsed_as_warsaw_and_passed_on_in_utc(db, monkeypatch):
    _seed_saka(db)
    seen = {}
    real = __import__("app.corroboration.cli", fromlist=["corroborate"]).corroborate

    def spy(engine, player, as_of, new_since=None, *, since=None, runtime):
        seen.update(as_of=as_of, new_since=new_since, since=since)
        return real(engine, player, as_of, new_since, since=since, runtime=runtime)

    monkeypatch.setattr("app.corroboration.cli.corroborate", spy)
    result = _run(
        _deps(db),
        "Saka",
        "--at",
        "2026-09-29T20:00",
        "--since",
        "2026-09-26T20:00",
        "--new-since",
        "2026-09-29T15:00",
    )
    assert result.exit_code == 0, result.output
    assert seen["as_of"] == NOW
    assert seen["since"] == parse_local("2026-09-26T20:00")
    assert seen["new_since"] == parse_local("2026-09-29T15:00")
    assert "2026-09-29 20:00" in result.stdout


def test_at_defaults_to_now(db, monkeypatch):
    _seed_saka(db)
    seen = {}
    real = __import__("app.corroboration.cli", fromlist=["corroborate"]).corroborate

    def spy(engine, player, as_of, new_since=None, *, since=None, runtime):
        seen["as_of"] = as_of
        return real(engine, player, as_of, new_since, since=since, runtime=runtime)

    monkeypatch.setattr("app.corroboration.cli.corroborate", spy)
    _run(_deps(db), "Saka")
    assert seen["as_of"] == NOW


def test_a_player_can_be_given_by_fpl_id_or_by_name(db):
    _seed_saka(db)
    by_id = _run(_deps(db), str(SAKA))
    by_name = _run(_deps(db), "saka")
    assert by_id.exit_code == by_name.exit_code == 0
    assert "Player: Saka" in by_id.stdout and "Player: Saka" in by_name.stdout


def test_an_ambiguous_name_lists_the_candidates_and_exits_1(db):
    seed_reference(db)
    result = _run(_deps(db), "Gabriel")
    assert result.exit_code == 1
    assert "ambiguous" in result.output.lower()
    assert str(GABRIEL_JESUS) in result.output and str(GABRIEL_MAGALHAES) in result.output


def test_an_unknown_player_exits_1(db):
    seed_reference(db)
    result = _run(_deps(db), "Nobody Atall")
    assert result.exit_code == 1
    assert "no player" in result.output.lower()


@pytest.mark.parametrize("option", ["--at", "--since", "--new-since"])
def test_an_invalid_time_exits_1_before_the_runtime_is_built(db, option):
    seed_reference(db)
    deps = _deps(db)
    result = _run(deps, "Saka", option, "yesterday")
    assert result.exit_code == 1
    assert f"{option} must be" in result.output
    assert deps.make_runtime.calls == 0


@pytest.mark.parametrize("since", ["2026-09-30T12:00", "2026-09-30T13:00"])
def test_since_not_earlier_than_at_exits_1(db, since):
    _seed_saka(db)
    deps = _deps(db)
    result = _run(deps, "Saka", "--at", "2026-09-30T12:00", "--since", since)
    assert result.exit_code == 1
    assert "--since must be earlier than --at" in result.output
    assert "No claim" not in result.output
    assert deps.make_runtime.calls == 0


def test_no_claim_message(db):
    seed_reference(db, {6: DEADLINE})
    add_claim(db, 1, ISAK, "out", mention="Isak")
    judge = _judge("supports")
    result = _run(_deps(db, CorroborationRuntime(embedder=FakeEmbedder(), judge=judge)), "Saka")
    assert result.exit_code == 0, result.output
    assert "No claim" in result.stdout
    assert "Anchor:" not in result.stdout


def test_without_a_key_the_retrieval_and_judge_are_skipped(db):
    _seed_saka(db)
    runtime = CorroborationRuntime(
        embedder=None, judge=None, skipped_reason="OPENROUTER_API_KEY is not set"
    )
    result = _run(_deps(db, runtime), "Saka")
    assert result.exit_code == 0, result.output
    assert "Retrieval and judge: skipped (OPENROUTER_API_KEY is not set)" in result.stdout
    assert "https://x.com/judged1/status/10" not in result.stdout


def test_the_judged_and_unjudged_counts_are_printed(db):
    _seed_saka(db)
    result = _run(_deps(db), "Saka")
    assert "Retrieval: ran; judged 1, unjudged 0" in result.stdout


def test_the_trace_line_appears_only_when_traced(db):
    _seed_saka(db)
    untraced = _run(_deps(db), "Saka")
    assert "trace:" not in untraced.stdout
    runtime = CorroborationRuntime(
        embedder=FakeEmbedder(),
        judge=_judge("supports"),
        tracer=LangfuseCorroborationTracer(FakeLangfuseClient()),
    )
    traced = _run(_deps(db, runtime), "Saka")
    assert "trace: trace-1" in traced.stdout


def test_a_repost_shows_its_original_author(db):
    seed_reference(db, {6: DEADLINE})
    add_claim(
        db,
        1,
        SAKA,
        "out",
        created_at=NOW - timedelta(hours=3),
        author="lister",
        is_repost=True,
        reposted_author_handle="origin",
    )
    result = _run(_deps(db), "Saka")
    assert "@lister (repost of @origin)" in result.stdout


def _traced_deps(db) -> tuple[CorroborationCliDeps, FakeLangfuseClient]:
    client = FakeLangfuseClient()
    runtime = CorroborationRuntime(
        embedder=FakeEmbedder(),
        judge=_judge("supports"),
        tracer=LangfuseCorroborationTracer(client),
    )
    return _deps(db, runtime), client


def test_the_runtime_is_built_once_and_the_tracer_flushed(db):
    _seed_saka(db)
    deps, client = _traced_deps(db)
    result = _run(deps, "Saka")
    assert result.exit_code == 0, result.output
    assert deps.make_runtime.calls == 1
    assert client.flushed == 1


def test_the_tracer_is_flushed_when_corroboration_raises(db, monkeypatch):
    _seed_saka(db)
    deps, client = _traced_deps(db)

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.corroboration.cli.corroborate", broken)
    result = _run(deps, "Saka")
    assert isinstance(result.exception, RuntimeError)
    assert client.flushed == 1


def test_the_default_runtime_without_a_key_skips_retrieval_and_the_judge():
    from app.corroboration.runtime import NOT_CONFIGURED, build_runtime

    runtime = build_runtime(CorroborationSettings(_env_file=None), FixedClock())
    assert runtime.embedder is None and runtime.judge is None
    assert runtime.skipped_reason == NOT_CONFIGURED


def test_the_default_runtime_with_a_key_builds_the_embedder_and_the_judge():
    from app.corroboration.runtime import build_runtime
    from app.llm.chat import DEFAULT_MODEL
    from app.retrieval.config import DEFAULT_EMBEDDING_MODEL

    settings = CorroborationSettings(_env_file=None, openrouter_api_key="dummy-key")
    runtime = build_runtime(settings, FixedClock())
    assert runtime.embedder is not None and runtime.embedder.model == DEFAULT_EMBEDDING_MODEL
    assert runtime.judge is not None and runtime.judge.model == DEFAULT_MODEL
    assert DEFAULT_MODEL in runtime.prices


def test_the_settings_add_no_variable_to_the_existing_ones():
    from app.llm.chat import ChatSettings
    from app.retrieval.config import RetrievalSettings

    assert set(CorroborationSettings.model_fields) == set(ChatSettings.model_fields) | set(
        RetrievalSettings.model_fields
    )
