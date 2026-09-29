import json
from collections import Counter
from pathlib import Path

from app.extraction import config
from app.extraction.cli import DEFAULT_RESULTS_DIR
from app.extraction.evaluation.selection import select_models, summaries_from_results
from app.extraction.model_settings import load_model_settings, pair_compatible
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


def _selection():
    return select_models(
        summaries_from_results(DEFAULT_RESULTS_DIR),
        lambda primary, fallback: pair_compatible(primary, fallback, load_model_settings()),
    )


def test_config_defaults_match_selection():
    selection = _selection()
    assert (config.DEFAULT_MODEL, config.DEFAULT_FALLBACK_MODEL or None) == (
        selection.default,
        selection.fallback,
    )


def test_adr_0006_names_the_defaults():
    text = ADR_0006.read_text()
    assert f"`{config.DEFAULT_MODEL}`" in text
    assert f"`{config.DEFAULT_FALLBACK_MODEL}`" in text
    if _selection().interim:
        assert "interim" in text


def test_report_names_every_test_run():
    report = (ADR_0006.parents[1] / "reports" / "extraction-eval-v1.md").read_text()
    names = [run["run_name"] for run in _test_runs()]
    assert names
    for name in names:
        assert name in report, name
    assert "total spend" in report
