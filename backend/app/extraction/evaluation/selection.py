"""The default and fallback model rule of SPEC 018 (ADR 0010), applied to the result files.

The cheapest model passing every threshold is the default. With none passing, it is the
model passing the most thresholds, then the higher F1, among those at or under the cost
limit. The fallback is the best other model within the limit that is pair-compatible with
the default (ADR 0006).
"""

from dataclasses import dataclass

from app.extraction.evaluation.metrics import MAX_MONTHLY_COST_PLN, threshold_flags
from app.llm.models import ModelSettings, pair_compatible

REPEAT_SUFFIX = "-r2"


@dataclass(frozen=True)
class Selection:
    default: str
    fallback: str | None


def _flags(run: dict) -> dict[str, bool]:
    metrics = run["metrics"]
    return threshold_flags(
        metrics["f1"],
        metrics["linking_accuracy"],
        metrics["false_alarm_rate"],
        metrics["projected_monthly_cost_pln"],
        metrics["errored_cases"],
    )


def _final_runs(runs: list[dict]) -> dict[str, dict]:
    """One run per model: the repeat, when there is one, replaces the first run."""
    final: dict[str, dict] = {}
    for run in sorted(runs, key=lambda r: r["run_name"].endswith(REPEAT_SUFFIX)):
        final[run["model"]] = run
    return final


def _within_cost_limit(run: dict) -> bool:
    cost = run["metrics"]["projected_monthly_cost_pln"]
    return cost is not None and cost <= MAX_MONTHLY_COST_PLN


def _quality(run: dict) -> tuple[int, float, float]:
    metrics = run["metrics"]
    return sum(_flags(run).values()), metrics["f1"], -metrics["projected_monthly_cost_pln"]


def choose_models(runs: list[dict], catalogue: dict[str, ModelSettings]) -> Selection | None:
    eligible = [run for run in _final_runs(runs).values() if _within_cost_limit(run)]
    if not eligible:
        return None
    passing = [run for run in eligible if all(_flags(run).values())]
    if passing:
        winner = min(passing, key=lambda r: r["metrics"]["projected_monthly_cost_pln"])
    else:
        winner = max(eligible, key=_quality)
    others = [
        run
        for run in eligible
        if run["model"] != winner["model"]
        and pair_compatible(winner["model"], run["model"], catalogue)
    ]
    fallback = max(others, key=_quality)["model"] if others else None
    return Selection(winner["model"], fallback)
