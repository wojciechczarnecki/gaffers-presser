import json
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from app.core.errors import ConfigError
from app.corroboration.config import DEFAULT_JUDGE_MODEL, CorroborationSettings
from app.corroboration.evaluation.cases import DEFAULT_RESULTS_DIR, write_cases
from app.corroboration.evaluation.cli import EvaluationCliDeps, app
from app.corroboration.evaluation.runner import (
    EvaluationError,
    default_result_path,
    run_evaluation,
)
from app.corroboration.judge import PROMPT_VERSION
from tests.corroboration.evaluation.fakes import ScriptedJudge
from tests.corroboration.evaluation.test_cases import make_case
from tests.retrieval.fakes import FakeEmbedder
from tests.retrieval.helpers import PRICES, FixedClock

NOW = datetime(2026, 10, 1, 9, 30, tzinfo=UTC)


def _cases():
    return [
        make_case(1, "supports", split="test", reviewed=True),
        make_case(2, "related", split="test", reviewed=False),
        make_case(3, "unrelated", split="dev", reviewed=True),
    ]


def _run(cases, judge, split="test", include=False):
    return run_evaluation(cases, judge, split, include, "fake/model", "judge@1", NOW)


def test_reviewed_cases_only_by_default():
    judge = ScriptedJudge("supports")
    data = _run(_cases(), judge)
    assert [o["id"] for o in data["outcomes"]] == ["jc-001"]
    assert len(judge.inputs) == 1
    assert data["include_unreviewed"] is False


def test_include_unreviewed_adds_them():
    data = _run(_cases(), ScriptedJudge("supports"), include=True)
    assert [o["id"] for o in data["outcomes"]] == ["jc-001", "jc-002"]
    assert data["include_unreviewed"] is True


def test_no_reviewed_case_in_the_split_is_an_error_naming_the_flag():
    cases = [make_case(1, split="test", reviewed=False)]
    with pytest.raises(EvaluationError, match="--include-unreviewed"):
        _run(cases, ScriptedJudge("supports"))
    with pytest.raises(EvaluationError, match="has no cases"):
        _run(cases, ScriptedJudge("supports"), split="dev")


def test_the_result_carries_the_run_details_and_per_case_outcomes():
    judge = ScriptedJudge(lambda item: "related" if item.post.text == "post 2" else "supports")
    data = _run(_cases(), judge, include=True)
    assert data["model"] == "fake/model"
    assert data["prompt_version"] == "judge@1"
    assert data["split"] == "test"
    assert data["date"] == NOW.isoformat()
    assert data["cost_usd"] == pytest.approx(0.002)
    assert data["metrics"]["accuracy"] == 1.0
    assert data["outcomes"][0] == {
        "id": "jc-001",
        "expected": "supports",
        "predicted": "supports",
        "error_class": None,
        "input_tokens": 10,
        "output_tokens": 5,
        "cost_usd": 0.001,
    }


def test_the_judge_receives_the_case_details():
    judge = ScriptedJudge("supports")
    _run(_cases(), judge)
    (item,) = judge.inputs
    assert item.player.web_name == "Isak"
    assert item.anchor.event_type == "doubt" and item.anchor.certainty == "likely"
    assert item.post.reposted_author_handle == "origin" and item.post.is_repost


def test_a_failing_case_is_reported_and_counts_as_wrong():
    judge = ScriptedJudge(lambda item: RuntimeError("down"))
    data = _run(_cases(), judge, include=True)
    assert [o["error_class"] for o in data["outcomes"]] == ["RuntimeError", "RuntimeError"]
    assert data["metrics"]["errored"] == 2
    assert data["metrics"]["accuracy"] == 0.0
    assert data["cost_usd"] is None


def test_the_default_output_path_names_the_split_and_the_model():
    path = default_result_path("test", "openai/gpt-6-luna")
    assert path == DEFAULT_RESULTS_DIR / "test-openai-gpt-6-luna.json"


def _run_with_cost(cases, judge, calls=None, rate=None):
    return run_evaluation(
        cases,
        judge,
        "test",
        True,
        "fake/model",
        "judge@1",
        NOW,
        calls_per_month=calls,
        usd_pln_rate=rate,
    )


def test_the_result_carries_the_production_slice():
    cases = [
        make_case(1, "supports", has_player_event=False),
        make_case(2, "unrelated", has_player_event=False),
        make_case(3, "supports", has_player_event=True),
    ]
    judge = ScriptedJudge(lambda item: "supports")
    data = _run(cases, judge, include=True)
    assert data["metrics"]["cases"] == 3
    assert data["production"]["cases"] == 2
    assert data["production"]["accuracy"] == 0.5
    assert data["production"]["false_support"]["numerator"] == 1
    assert data["verdict"]["passed"] is False


def test_projected_monthly_cost():
    cases = [make_case(1, "supports")]
    judge = ScriptedJudge("supports", cost_usd=0.002)
    data = _run_with_cost(cases, judge, calls=1000, rate=4.0)
    assert data["projected_monthly_cost_pln"] == pytest.approx(0.002 * 1000 * 4.0)
    assert _run_with_cost(cases, judge, calls=None, rate=4.0)["projected_monthly_cost_pln"] is None
    assert _run_with_cost(cases, judge, calls=1000, rate=None)["projected_monthly_cost_pln"] is None


def test_evaluate_prints_and_writes_the_verdict(tmp_path):
    result = _invoke(tmp_path, _deps(), "--split", "test")
    assert result.exit_code == 0, result.output
    assert "threshold accuracy: pass" in result.stdout
    assert "threshold false_support: fail" in result.stdout
    assert "threshold contradicts_recall: fail" in result.stdout
    assert "verdict: FAIL" in result.stdout
    assert "production slice" in result.stdout
    data = json.loads((tmp_path / "result.json").read_text())
    assert data["verdict"]["passed"] is False
    assert "production" in data


def test_v2_result_paths():
    assert default_result_path("test", "a/b", "v1") == DEFAULT_RESULTS_DIR / "test-a-b.json"
    assert default_result_path("test", "a/b", "v2") == DEFAULT_RESULTS_DIR / "v2" / "test-a-b.json"
    assert (
        default_result_path("dev", "a/b", "v2")
        == DEFAULT_RESULTS_DIR / "v2" / "dev" / "dev-a-b.json"
    )


def _deps(judge=None, models=None, settings=None):
    models = models if models is not None else {}

    def make_judge(model: str):
        if model == "unknown/model":
            raise ConfigError("--model names a model with no entry in model_settings.toml")
        judge_for = judge or ScriptedJudge("supports", model=model)
        models[model] = judge_for
        return judge_for

    return EvaluationCliDeps(
        engine=None,
        settings=settings or CorroborationSettings(_env_file=None, llm_model=""),
        clock=FixedClock(NOW),
        make_embedder=lambda: FakeEmbedder(),
        make_judge=make_judge,
        prices=PRICES,
    )


def _invoke(tmp_path, deps, *args):
    path = tmp_path / "cases.jsonl"
    if not path.exists():
        write_cases(path, _cases())
    return CliRunner().invoke(
        app,
        ["evaluate", "--cases", str(path), "--output", str(tmp_path / "result.json"), *args],
        obj=deps,
    )


def test_evaluate_prints_the_metrics_and_writes_the_file(tmp_path):
    result = _invoke(tmp_path, _deps(), "--split", "test")
    assert result.exit_code == 0, result.output
    assert "accuracy: 1.000" in result.stdout
    assert "false-support rate" in result.stdout
    assert "supports: precision" in result.stdout
    data = json.loads((tmp_path / "result.json").read_text())
    assert data["model"] == DEFAULT_JUDGE_MODEL
    assert data["prompt_version"] == PROMPT_VERSION
    assert len(data["outcomes"]) == 1


def test_evaluate_model_runs_the_judge_on_that_model(tmp_path):
    models: dict = {}
    result = _invoke(tmp_path, _deps(models=models), "--model", "other/model")
    assert result.exit_code == 0, result.output
    assert list(models) == ["other/model"]
    assert json.loads((tmp_path / "result.json").read_text())["model"] == "other/model"


def test_evaluate_defaults_to_the_judge_model_setting(tmp_path):
    models: dict = {}
    settings = CorroborationSettings(_env_file=None, llm_judge_model="env/model")
    result = _invoke(tmp_path, _deps(models=models, settings=settings))
    assert result.exit_code == 0, result.output
    assert list(models) == ["env/model"]
    assert json.loads((tmp_path / "result.json").read_text())["model"] == "env/model"


def test_a_model_outside_the_catalogue_exits_1(tmp_path):
    result = _invoke(tmp_path, _deps(), "--model", "unknown/model")
    assert result.exit_code == 1
    assert "model_settings.toml" in result.output


def test_no_reviewed_cases_exits_1_and_names_the_flag(tmp_path):
    write_cases(tmp_path / "cases.jsonl", [make_case(1, split="test", reviewed=False)])
    result = _invoke(tmp_path, _deps())
    assert result.exit_code == 1
    assert "--include-unreviewed" in result.output


def test_a_bad_split_exits_1(tmp_path):
    assert _invoke(tmp_path, _deps(), "--split", "prod").exit_code == 1


def test_evaluate_writes_header_and_guard_refuses(tmp_path):
    set_dir = tmp_path / "v1"
    set_dir.mkdir()
    write_cases(set_dir / "cases.jsonl", _cases())
    output = tmp_path / "result.json"
    args = ["evaluate", "--cases", str(set_dir / "cases.jsonl"), "--output", str(output)]
    first = CliRunner().invoke(app, args, obj=_deps())
    assert first.exit_code == 0, first.output
    header = json.loads(output.read_text())["header"]
    assert header["set_version"] == "v1"
    assert header["model"] == DEFAULT_JUDGE_MODEL
    assert header["prompt_version"] == PROMPT_VERSION
    assert header["date"] == NOW.isoformat()
    assert header["judge"] is None

    again = CliRunner().invoke(app, args, obj=_deps())
    assert again.exit_code == 1
    assert str(output) in again.output
    forced = CliRunner().invoke(app, [*args, "--force"], obj=_deps())
    assert forced.exit_code == 0, forced.output
