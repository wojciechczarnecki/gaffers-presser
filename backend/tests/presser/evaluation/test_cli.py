import json
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from app.llm.pricing import load_prices
from app.llm.structured import StructuredCaller
from app.presser.evaluation.cases import write_cases
from app.presser.evaluation.cli import EvaluationCliDeps, app
from app.presser.evaluation.judge import (
    JudgedClaim,
    JudgeVerdict,
    build_presser_judge,
)
from app.presser.writer import PresserDraft, build_writer
from tests.delivery.fakes import FixedClock
from tests.extraction.fakes import FakeChatModel
from tests.presser.evaluation.factories import case


def verdict(*labels: str) -> JudgeVerdict:
    return JudgeVerdict(
        claims=[JudgedClaim(claim=f"claim {n}", label=label) for n, label in enumerate(labels)]
    )


def deps(writer_responses=(), judge_responses=()):
    clock = FixedClock(datetime(2026, 10, 9, 12, 0, tzinfo=UTC))
    writer_fake = FakeChatModel(responses=list(writer_responses))
    judge_fake = FakeChatModel(responses=list(judge_responses))
    built = EvaluationCliDeps(
        engine=None,
        clock=clock,
        make_writer=lambda model: build_writer(
            StructuredCaller(writer_fake, model, load_prices(), clock)
        ),
        make_judge=lambda model: build_presser_judge(
            StructuredCaller(judge_fake, model, load_prices(), clock)
        ),
    )
    return built, writer_fake, judge_fake


def invoke(built, *args, input=None):
    return CliRunner().invoke(app, list(args), obj=built, input=input)


@pytest.fixture
def cases_path(tmp_path):
    path = tmp_path / "cases.jsonl"
    write_cases(path, [case("c1", "dev"), case("c2", "dev"), case("c3", "test")])
    return path


def test_evaluate_writes_result(cases_path, tmp_path):
    built, _, _ = deps(
        [PresserDraft(text="Pierwszy"), PresserDraft(text="Drugi")],
        [verdict("supported", "unsupported"), verdict("supported")],
    )
    output = tmp_path / "run.json"
    result = invoke(
        built, "evaluate", "--split", "dev", "--cases", str(cases_path), "--output", str(output)
    )
    assert result.exit_code == 0, result.output
    data = json.loads(output.read_text(encoding="utf-8"))
    assert [p["case_id"] for p in data["pressers"]] == ["c1", "c2"]
    assert [p["faithfulness"] for p in data["pressers"]] == [0.5, 1.0]
    assert data["totals"]["faithfulness"] == 0.75
    assert data["model"] == "openai/gpt-6-luna" and data["judge_model"] == "openai/gpt-6.1-sol"
    assert "faithfulness: 0.750" in result.stdout
    assert f"result: {output}" in result.stdout


def test_evaluate_rejects_a_bad_split(cases_path):
    built, _, _ = deps()
    result = invoke(built, "evaluate", "--split", "train", "--cases", str(cases_path))
    assert result.exit_code == 1
    assert "dev or test" in result.stderr


def test_evaluate_reports_a_model_outside_the_catalogue(cases_path, tmp_path):
    from app.core.errors import ConfigError

    def refuse(model):
        raise ConfigError("--model names a model with no entry in model_settings.toml")

    built, _, _ = deps()
    built = EvaluationCliDeps(built.engine, built.clock, refuse, built.make_judge)
    result = invoke(
        built, "evaluate", "--split", "dev", "--cases", str(cases_path), "--model", "x/unknown"
    )
    assert result.exit_code == 1
    assert "model_settings.toml" in result.stderr
