import json

from app.corroboration.evaluation.cases import (
    DEFAULT_CASES_PATH,
    DEFAULT_RESULTS_DIR,
    LABELS,
    load_cases,
)
from app.corroboration.judge import PROMPT_VERSION
from app.llm.chat import DEFAULT_MODEL

RESULT = DEFAULT_RESULTS_DIR / f"test-{DEFAULT_MODEL.replace('/', '-')}.json"


def test_the_first_run_of_the_default_judge_on_the_test_split_is_committed():
    assert RESULT.is_file(), f"{RESULT.name} is missing"
    data = json.loads(RESULT.read_text())
    assert data["model"] == DEFAULT_MODEL
    assert data["split"] == "test"
    assert data["include_unreviewed"] is True
    assert data["prompt_version"] == PROMPT_VERSION


def test_the_result_holds_the_metrics_and_one_outcome_per_test_case():
    data = json.loads(RESULT.read_text())
    metrics = data["metrics"]
    assert {"cases", "errored", "accuracy", "per_label", "false_support"} <= set(metrics)
    assert set(metrics["per_label"]) == set(LABELS)
    assert {"numerator", "denominator", "rate"} == set(metrics["false_support"])
    test_ids = sorted(c.id for c in load_cases(DEFAULT_CASES_PATH) if c.split == "test")
    assert sorted(o["id"] for o in data["outcomes"]) == test_ids
    assert metrics["cases"] == len(test_ids)
    assert data["cost_usd"] is None or data["cost_usd"] >= 0
