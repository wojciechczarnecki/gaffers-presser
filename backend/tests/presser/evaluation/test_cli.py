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


def test_evaluate_never_overwrites_a_run_without_force(cases_path, tmp_path):
    output = tmp_path / "run.json"
    output.write_text('{"reviews": "kept"}', encoding="utf-8")
    built, writer_fake, _ = deps(
        [PresserDraft(text="Pierwszy"), PresserDraft(text="Drugi")],
        [verdict("supported"), verdict("supported")],
    )
    args = ("evaluate", "--split", "dev", "--cases", str(cases_path), "--output", str(output))
    refused = invoke(built, *args)
    assert refused.exit_code == 1
    assert "--force" in refused.stderr
    assert output.read_text(encoding="utf-8") == '{"reviews": "kept"}'
    assert writer_fake.received_messages == []
    forced = invoke(built, *args, "--force")
    assert forced.exit_code == 0, forced.output
    assert [p["text"] for p in read(output)["pressers"]] == ["Pierwszy", "Drugi"]


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


def recorded_run(tmp_path, claim_labels=("supported", "unsupported", "supported")):
    built, _, _ = deps(
        [PresserDraft(text="Pierwszy"), PresserDraft(text="Drugi")],
        [verdict(*claim_labels[:2]), verdict(*claim_labels[2:])],
    )
    path = tmp_path / "cases.jsonl"
    write_cases(path, [case("c1", "dev", tags=["tie_win"]), case("c2", "dev")])
    output = tmp_path / "run.json"
    result = invoke(
        built, "evaluate", "--split", "dev", "--cases", str(path), "--output", str(output)
    )
    assert result.exit_code == 0, result.output
    return output, path


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_review_records_rating_and_note(tmp_path):
    run, cases_path = recorded_run(tmp_path)
    result = invoke(
        EvaluationCliDeps(None, FixedClock(), lambda m: None, lambda m: None),
        "review",
        "--run",
        str(run),
        "--cases",
        str(cases_path),
        input="4\nfajne\n2\n3\n\n\n",
    )
    assert result.exit_code == 0, result.output
    styles = [p["style"] for p in read(run)["pressers"]]
    assert styles == [
        {"rating": 4, "note": "fajne", "inflection_errors": 2},
        {"rating": 3, "note": None, "inflection_errors": 0},
    ]
    assert "rated: 2/2  average: 3.50  inflection errors: 1.00" in result.stdout
    assert "tags: tie_win" in result.stdout
    assert "winners: Bartas 60" in result.stdout
    assert "Pierwszy" in result.stdout


def test_review_rejects_out_of_range(tmp_path):
    run, cases_path = recorded_run(tmp_path)
    result = invoke(
        None,
        "review",
        "--run",
        str(run),
        "--cases",
        str(cases_path),
        input="7\n5\n\n\n3\n\n\n",
    )
    assert "unknown rating '7'" in result.stdout
    assert [p["style"]["rating"] for p in read(run)["pressers"]] == [5, 3]


def test_review_skips_quits_and_resumes(tmp_path):
    run, cases_path = recorded_run(tmp_path)
    first = invoke(None, "review", "--run", str(run), "--cases", str(cases_path), input="s\nq\n")
    assert "rated: 0/2" in first.stdout
    assert all(p["style"] is None for p in read(run)["pressers"])
    invoke(None, "review", "--run", str(run), "--cases", str(cases_path), input="4\n\n\nq\n")
    again = invoke(None, "review", "--run", str(run), "--cases", str(cases_path), input="3\n\n\n")
    assert [p["style"]["rating"] for p in read(run)["pressers"]] == [4, 3]
    assert "1/1" in again.stdout
    rerun = invoke(None, "review", "--run", str(run), "--cases", str(cases_path))
    assert "nothing to review" in rerun.stdout
    everything = invoke(
        None,
        "review",
        "--run",
        str(run),
        "--all",
        "--cases",
        str(cases_path),
        input="1\n\n\n5\n\n\n",
    )
    assert "rated: 2/2  average: 3.00" in everything.stdout


def test_review_records_inflection_errors(tmp_path):
    run, cases_path = recorded_run(tmp_path)
    invoke(
        None,
        "review",
        "--run",
        str(run),
        "--cases",
        str(cases_path),
        input="4\nfajne\n2\n3\n\n\n",
    )
    assert [p["style"]["inflection_errors"] for p in read(run)["pressers"]] == [2, 0]

    (tmp_path / "second").mkdir()
    other, other_cases = recorded_run(tmp_path / "second")
    result = invoke(
        None,
        "review",
        "--run",
        str(other),
        "--cases",
        str(other_cases),
        input="5\n\nx\n-1\n²\n1\n3\n\n\n",
    )
    assert result.exit_code == 0, result.output
    assert "unknown count '²'" in result.stdout
    assert "inflection errors" in result.stdout
    assert [p["style"]["inflection_errors"] for p in read(other)["pressers"]] == [1, 0]


def test_judge_review_records_verdicts_and_agreement(tmp_path):
    run, _ = recorded_run(tmp_path)
    result = invoke(None, "judge-review", "--run", str(run), input="a\nf\na\n")
    assert result.exit_code == 0, result.output
    data = read(run)
    claims = [c for p in data["pressers"] for c in p["claims"]]
    assert [c["owner_label"] for c in claims] == ["supported", "supported", "supported"]
    assert "reviewed claims: 3  agreement: 0.67" in result.stdout
    assert data["totals"]["judge_agreement"] == pytest.approx(2 / 3)
    assert data["totals"]["reviewed_claims"] == 3


def test_judge_review_limit_skip_and_quit(tmp_path):
    run, _ = recorded_run(tmp_path)
    limited = invoke(None, "judge-review", "--run", str(run), "--limit", "1", input="s\na\n")
    data = read(run)
    assert [c["owner_label"] for c in data["pressers"][0]["claims"]] == [None, "unsupported"]
    assert "reviewed claims: 1  agreement: 1.00" in limited.stdout
    quit_run = invoke(None, "judge-review", "--run", str(run), input="q\n")
    assert quit_run.exit_code == 0
    assert read(run)["pressers"][0]["claims"][0]["owner_label"] is None
    assert read(run)["pressers"][1]["claims"][0]["owner_label"] is None
    resumed = invoke(None, "judge-review", "--run", str(run), input="a\nf\n")
    assert resumed.stdout.count("claim: ") == 2
    claims = [c["owner_label"] for p in read(run)["pressers"] for c in p["claims"]]
    assert claims == ["supported", "unsupported", "unsupported"]
    assert "nothing to review" in invoke(None, "judge-review", "--run", str(run)).stdout


def test_summary_reports_and_applies_thresholds(tmp_path):
    from tests.presser.evaluation.test_summary import presser, run, write

    write(tmp_path, run("cheap/model", [presser(n, rating=3, cost=0.0001) for n in range(4)]))
    write(tmp_path, run("mid/model", [presser(n, cost=0.002) for n in range(4)]))
    result = invoke(None, "summary", "--results-dir", str(tmp_path))
    assert result.exit_code == 0
    lines = result.stdout.splitlines()
    assert any(line.startswith("cheap/model") and line.endswith("fail") for line in lines)
    assert any(line.startswith("mid/model") and line.endswith("PASS") for line in lines)
    assert lines[-1] == "winner: mid/model"


def test_summary_without_runs_or_winner(tmp_path):
    from tests.presser.evaluation.test_summary import presser, run, write

    assert "no test runs" in invoke(None, "summary", "--results-dir", str(tmp_path)).stdout
    assert "no test runs" in invoke(None, "summary", "--results-dir", str(tmp_path / "no")).stdout
    write(tmp_path, run("a/m", [presser(n, rating=2) for n in range(2)]))
    assert invoke(None, "summary", "--results-dir", str(tmp_path)).stdout.splitlines()[-1] == (
        "no model passes"
    )


def test_writer_gets_temperature_and_judge_zero(monkeypatch):
    seen: list[tuple[str, float]] = []

    def recorder(settings, clock, model, temperature):
        seen.append((model, temperature))
        return StructuredCaller(FakeChatModel(responses=[]), model, load_prices(), clock)

    monkeypatch.setattr("app.presser.evaluation.cli._caller", recorder)
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    from app.presser.evaluation.cli import _deps_from_settings

    built = _deps_from_settings()
    built.make_writer("w")
    built.make_judge("j")
    assert seen == [("w", 0.8), ("j", 0.0)]
