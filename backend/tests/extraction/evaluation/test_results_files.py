import json
import re
from collections import Counter
from pathlib import Path

from app.extraction.evaluation.dataset import DEFAULT_RESULTS_DIR
from app.llm import chat as config
from tests.extraction.candidates import CANDIDATES

ADR_0006 = (
    Path(__file__).resolve().parents[4]
    / "docs"
    / "adr"
    / "0006-default-extraction-model-openrouter.md"
)


def _test_runs() -> list[dict]:
    return [json.loads(path.read_text()) for path in sorted(DEFAULT_RESULTS_DIR.glob("*.json"))]


def test_one_test_run_per_candidate_with_one_prompt():
    runs = _test_runs()
    assert runs, "no committed test-split run files"
    assert {run["split"] for run in runs} == {"test"}
    assert len({run["prompt_version"] for run in runs}) == 1

    names_by_model: dict[str, list[str]] = {}
    for run in runs:
        names_by_model.setdefault(run["model"], []).append(run["run_name"])
    assert set(names_by_model) == set(CANDIDATES)
    for model, names in names_by_model.items():
        plain = [n for n in names if not n.endswith("-r2")]
        reruns = [n for n in names if n.endswith("-r2")]
        assert len(plain) == 1, model
        assert len(reruns) <= 1, model
    assert Counter(run["reasoning_effort"] for run in runs).keys() <= {"none", "low"}


def test_adr_0006_names_the_defaults():
    text = ADR_0006.read_text()
    assert f"`{config.DEFAULT_MODEL}`" in text
    assert f"`{config.DEFAULT_FALLBACK_MODEL}`" in text


def test_report_names_every_test_run():
    report = (ADR_0006.parents[1] / "reports" / "extraction-eval-v1.md").read_text()
    names = [run["run_name"] for run in _test_runs()]
    assert names
    for name in names:
        assert name in report, name
    assert "total spend" in report


V2_RESULTS_DIR = DEFAULT_RESULTS_DIR / "v2"
THREE_PROMPTS = re.compile(r"^extraction@\d+\+link_disambiguation@\d+\+match_check@\d+$")


def _v2_test_runs() -> list[dict]:
    return [json.loads(path.read_text()) for path in sorted(V2_RESULTS_DIR.glob("*.json"))]


def test_v2_one_test_run_per_candidate():
    runs = _v2_test_runs()
    assert runs, "no committed v2 test-split run files"
    assert {run["split"] for run in runs} == {"test"}

    names_by_model: dict[str, list[str]] = {}
    for run in runs:
        header = run["header"]
        assert header["set_version"] == "v2"
        assert header["model"] == run["model"]
        assert header["reasoning_effort"] == run["reasoning_effort"]
        assert header["git_commit"]
        assert THREE_PROMPTS.match(header["prompt_version"]), header["prompt_version"]
        names_by_model.setdefault(run["model"], []).append(run["run_name"])
    assert set(names_by_model) == set(CANDIDATES)
    for model, names in names_by_model.items():
        plain = [n for n in names if not n.endswith("-r2")]
        reruns = [n for n in names if n.endswith("-r2")]
        assert len(plain) == 1, model
        assert len(reruns) <= 1, model


def test_v2_test_runs_share_one_prompt():
    versions = {run["header"]["prompt_version"] for run in _v2_test_runs()}
    assert len(versions) == 1, versions
    assert {run["prompt_version"] for run in _v2_test_runs()} == versions


def test_v2_test_spend_within_cap():
    total = sum(run["metrics"]["total_cost_usd"] for run in _v2_test_runs())
    assert total <= 5


ADR_0010 = ADR_0006.with_name("0010-extraction-match-check-and-default-model.md")


def test_v2_rule_picks_the_configured_defaults():
    from app.extraction.evaluation.selection import choose_models
    from app.llm.models import load_model_settings

    selection = choose_models(_v2_test_runs(), load_model_settings())
    assert selection is not None
    assert (selection.default, selection.fallback) == (
        config.DEFAULT_MODEL,
        config.DEFAULT_FALLBACK_MODEL,
    )


def test_adr_0010_names_the_defaults():
    text = ADR_0010.read_text()
    assert f"`{config.DEFAULT_MODEL}`" in text
    assert f"`{config.DEFAULT_FALLBACK_MODEL}`" in text
    for run in _v2_test_runs():
        assert f"`{run['model']}`" in text, run["model"]


def test_report_v2_names_every_test_run():
    report = (ADR_0006.parents[1] / "reports" / "extraction-eval-v2.md").read_text()
    names = [run["run_name"] for run in _v2_test_runs()]
    assert names
    for name in names:
        assert name in report, name
    assert "total spend" in report.lower()
