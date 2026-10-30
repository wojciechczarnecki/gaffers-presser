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


def _cost_rank(data: dict[str, Any]) -> tuple[bool, float]:
    cost = data["projected_monthly_cost_pln"]
    return (cost is None, cost or 0.0)


def choose_judge(runs: Mapping[str, dict[str, Any]]) -> str | None:
    final = _final_runs(runs)
    passing = {model: d for model, d in final.items() if d["verdict"]["passed"]}
    if passing:
        # A passing run with no projected cost still passes; it ranks after the priced ones.
        return min(passing, key=lambda m: (_cost_rank(passing[m]), m))
    priced = {model: d for model, d in final.items() if d["projected_monthly_cost_pln"] is not None}
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
