import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.extraction.evaluation.metrics import MAX_MONTHLY_COST_PLN


@dataclass(frozen=True)
class RunSummary:
    model: str
    run_name: str
    passes: bool
    thresholds_passed: dict[str, bool]
    f1: float
    monthly_cost_pln: float | None


@dataclass(frozen=True)
class Selection:
    default: str
    fallback: str | None
    interim: bool
    missed: tuple[str, ...]


def _within_budget(run: RunSummary) -> bool:
    return run.monthly_cost_pln is not None and run.monthly_cost_pln <= MAX_MONTHLY_COST_PLN


def _best_first(runs: Sequence[RunSummary]) -> list[RunSummary]:
    return sorted(runs, key=lambda r: (-sum(r.thresholds_passed.values()), -r.f1))


def select_models(runs: Sequence[RunSummary]) -> Selection:
    passing = [r for r in runs if r.passes]
    if len(passing) >= 2:
        cheapest = sorted(passing, key=lambda r: (r.monthly_cost_pln, -r.f1))
        return Selection(cheapest[0].model, cheapest[1].model, False, ())

    affordable = _best_first([r for r in runs if _within_budget(r)])
    if len(passing) == 1:
        winner = passing[0]
        others = [r for r in affordable if r.model != winner.model]
        return Selection(winner.model, others[0].model if others else None, False, ())

    if not affordable:
        raise ValueError(f"no candidate projects to at most {MAX_MONTHLY_COST_PLN:g} PLN a month")
    default = affordable[0]
    missed = tuple(name for name, ok in default.thresholds_passed.items() if not ok)
    fallback = affordable[1].model if len(affordable) > 1 else None
    return Selection(default.model, fallback, True, missed)


def summaries_from_results(results_dir: Path) -> list[RunSummary]:
    chosen: dict[str, RunSummary] = {}
    for path in sorted(Path(results_dir).glob("*.json")):
        data = json.loads(path.read_text())
        if data.get("split") != "test":
            continue
        metrics = data["metrics"]
        summary = RunSummary(
            model=data["model"],
            run_name=data["run_name"],
            passes=bool(metrics["passes"]),
            thresholds_passed=dict(metrics["thresholds_passed"]),
            f1=metrics["f1"],
            monthly_cost_pln=metrics["projected_monthly_cost_pln"],
        )
        is_rerun = summary.run_name.endswith("-r2")
        if summary.model not in chosen or is_rerun:
            chosen[summary.model] = summary
    return list(chosen.values())
