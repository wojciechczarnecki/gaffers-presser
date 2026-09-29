import json
from collections import Counter

from app.extraction.cli import DEFAULT_RESULTS_DIR
from tests.extraction.candidates import CANDIDATES


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
