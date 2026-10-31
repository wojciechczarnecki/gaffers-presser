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


V3_RESULTS_DIR = DEFAULT_RESULTS_DIR / "v3"
V3_RUN = "test-v3-openai-gpt-6-luna"
DEV_ITERATIONS = (
    ADR_0006.parents[2] / "specs" / "020-availability-and-match-check" / "dev-iterations.md"
)


def _v3_test_runs() -> list[dict]:
    return [json.loads(path.read_text()) for path in sorted(V3_RESULTS_DIR.glob("*.json"))]


def test_v3_one_test_run():
    runs = _v3_test_runs()
    names = sorted(run["run_name"] for run in runs)
    assert names in ([V3_RUN], [V3_RUN, f"{V3_RUN}-r2"])
    for run in runs:
        assert run["split"] == "test"
        assert run["header"]["set_version"] == "v3"
        assert run["header"]["prompt_version"] == "extraction@6+link_disambiguation@1+match_check@2"


def test_v3_test_run_passes():
    from app.extraction.evaluation.metrics import (
        MAX_INJURIES_DROPPED,
        MAX_OTHER_MATCH_ABSENCE_FALSE_ALARMS,
        threshold_flags,
    )

    final = _v3_test_runs()[-1]
    m = final["metrics"]
    flags = threshold_flags(
        m["f1"],
        m["linking_accuracy"],
        m["false_alarm_rate"],
        m["projected_monthly_cost_pln"],
        m["errored_cases"],
    )
    assert all(flags.values()), flags
    assert m["other_match_absence_false_alarms"] <= MAX_OTHER_MATCH_ABSENCE_FALSE_ALARMS
    assert m["injuries_dropped_by_check"] <= MAX_INJURIES_DROPPED


def _log_spend(label: str) -> float:
    found = re.findall(rf"^{label}: ([0-9.]+) USD", DEV_ITERATIONS.read_text(), re.MULTILINE)
    return sum(float(value) for value in found)


def test_v3_spend_within_cap():
    total = sum(run["metrics"]["total_cost_usd"] for run in _v3_test_runs())
    total += _log_spend("Extraction dev spend")
    total += _log_spend("Corroboration regression spend")
    total += _log_spend("Discarded test spend")
    assert total <= 5


def test_report_v3_names_the_test_run():
    report = (ADR_0006.parents[1] / "reports" / "extraction-eval-v3.md").read_text()
    final = _v3_test_runs()[-1]
    assert final["run_name"] in report
    assert f"{final['metrics']['f1']:.3f}" in report
