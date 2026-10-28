from collections.abc import Mapping
from typing import Any

MAX_MONTHLY_COST_PLN = 5.0
THRESHOLD_NAMES = ("accuracy", "false_support", "contradicts_recall", "no_errors")
REPEAT_SUFFIX = "-r2"


def _final_runs(runs: Mapping[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    # The key is a result file's stem; a repeat (`-r2`) replaces the model's first run.
    final: dict[str, dict[str, Any]] = {}
    for _, data in sorted(runs.items(), key=lambda item: item[0].endswith(REPEAT_SUFFIX)):
        final[data["model"]] = data
    return final


def _passed_count(data: dict[str, Any]) -> int:
    return sum(1 for name in THRESHOLD_NAMES if data["verdict"][name])


def _false_support(data: dict[str, Any]) -> float:
    rate = data["metrics"]["false_support"]["rate"]
    return 1.0 if rate is None else rate


def choose_judge(runs: Mapping[str, dict[str, Any]]) -> str | None:
    final = _final_runs(runs)
    priced = {model: d for model, d in final.items() if d["projected_monthly_cost_pln"] is not None}
    passing = {model: d for model, d in priced.items() if d["verdict"]["passed"]}
    if passing:
        return min(passing, key=lambda m: (passing[m]["projected_monthly_cost_pln"], m))
    eligible = {
        model: d
        for model, d in priced.items()
        if d["projected_monthly_cost_pln"] <= MAX_MONTHLY_COST_PLN
    }
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda m: (
            -_passed_count(eligible[m]),
            _false_support(eligible[m]),
            eligible[m]["projected_monthly_cost_pln"],
            m,
        ),
    )
