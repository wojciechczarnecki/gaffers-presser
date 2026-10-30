import json
import re
from pathlib import Path

from app.corroboration.config import DEFAULT_JUDGE_MODEL
from app.corroboration.evaluation.cases import (
    DEFAULT_RESULTS_DIR,
    LABELS,
    V1_CASES_PATH,
    load_cases,
)
from app.corroboration.evaluation.selection import choose_judge
from app.corroboration.judge import PROMPT_VERSION
from app.llm.chat import DEFAULT_MODEL
from tests.extraction.candidates import CANDIDATES

RESULT = DEFAULT_RESULTS_DIR / f"test-{DEFAULT_MODEL.replace('/', '-')}.json"


def test_the_first_run_of_the_default_judge_on_the_test_split_is_committed():
    assert RESULT.is_file(), f"{RESULT.name} is missing"
    data = json.loads(RESULT.read_text())
    assert data["model"] == DEFAULT_MODEL
    assert data["split"] == "test"
    assert data["include_unreviewed"] is True
    assert data["prompt_version"] == "corroboration_judge@1"


def test_the_result_holds_the_metrics_and_one_outcome_per_test_case():
    data = json.loads(RESULT.read_text())
    metrics = data["metrics"]
    assert {"cases", "errored", "accuracy", "per_label", "false_support"} <= set(metrics)
    assert set(metrics["per_label"]) == set(LABELS)
    assert {"numerator", "denominator", "rate"} == set(metrics["false_support"])
    test_ids = sorted(c.id for c in load_cases(V1_CASES_PATH) if c.split == "test")
    assert sorted(o["id"] for o in data["outcomes"]) == test_ids
    assert metrics["cases"] == len(test_ids)
    assert data["cost_usd"] is None or data["cost_usd"] >= 0


# --- set v2 -----------------------------------------------------------------

V2_RESULTS = DEFAULT_RESULTS_DIR / "v2"
V2_SET = Path(__file__).resolve().parents[3] / "evals" / "corroboration" / "v2"
DEV_LOG = (
    Path(__file__).resolve().parents[4]
    / "specs"
    / "019-corroboration-evaluation-v2"
    / "dev-iterations.md"
)


def _v2_files() -> list[Path]:
    return sorted(V2_RESULTS.glob("test-*.json"))


def _slug(model: str) -> str:
    return model.replace("/", "-")


def test_v2_one_test_run_per_candidate():
    names = {p.name for p in _v2_files()}
    for model in CANDIDATES:
        first = f"test-{_slug(model)}.json"
        assert first in names, f"{first} is missing"
    allowed = {f"test-{_slug(m)}{suffix}.json" for m in CANDIDATES for suffix in ("", "-r2")}
    assert names <= allowed
    for path in _v2_files():
        data = json.loads(path.read_text())
        header = data["header"]
        assert header["set_version"] == "v2"
        assert header["model"] == data["model"]
        assert header["prompt_version"]
        assert "reasoning_effort" in header
        assert header["git_commit"]
        assert header["git_dirty"] is False
        assert data["split"] == "test"
        assert "verdict" in data and "production" in data


def test_v2_test_runs_share_one_prompt():
    versions = {json.loads(p.read_text())["prompt_version"] for p in _v2_files()}
    assert versions == {PROMPT_VERSION}


def test_v2_spend_within_cap():
    build = json.loads((V2_SET / "set.json").read_text())["build"]
    spend = (
        build["embeddings_cost_usd"]
        + build["extraction_cost_usd"]
        + build["query_embeddings_cost_usd"]
    )
    log = DEV_LOG.read_text()
    for line in ("Dev spend", "Discarded test spend"):
        match = re.search(rf"^{line}: ([0-9.]+) USD$", log, re.MULTILINE)
        assert match, f"dev-iterations.md has no '{line}: <x> USD' line"
        spend += float(match.group(1))
    spend += sum(json.loads(p.read_text())["cost_usd"] or 0 for p in _v2_files())
    assert spend <= 5.00


def test_v2_rule_picks_qwen_and_the_owner_overrode_it():
    # the rule's answer stays recorded; the owner chose gemini as the default (ADR 0011)
    runs = {p.stem: json.loads(p.read_text()) for p in _v2_files()}
    assert choose_judge(runs) == "qwen/qwen3.8-flash"
    assert DEFAULT_JUDGE_MODEL == "google/gemini-3.1-flash-lite"


def test_report_v2_names_every_test_run():
    report = (V2_SET.parents[3] / "docs" / "reports" / "corroboration-eval-v2.md").read_text(
        encoding="utf-8"
    )
    final: dict[str, dict] = {}
    for path in sorted(_v2_files(), key=lambda p: p.stem.endswith("-r2")):
        data = json.loads(path.read_text())
        final[data["model"]] = data
    assert set(final) == set(CANDIDATES)
    for model, data in final.items():
        assert f"`{model}`" in report, model
        assert f"{data['metrics']['accuracy']:.3f}" in report, model
