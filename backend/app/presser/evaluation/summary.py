import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.presser.evaluation.runner import compute_totals

FAITHFULNESS_THRESHOLD = 0.95
STYLE_THRESHOLD = 3.5
EPSILON = 1e-9


@dataclass(frozen=True)
class RunSummary:
    model: str
    pressers: int
    errored: int
    faithfulness: float | None
    style: float | None
    rated: int
    within_limit_share: float | None
    avg_cost_usd: float | None
    avg_latency_seconds: float | None
    judge_agreement: float | None
    passes: bool


def summarise_run(data: dict) -> RunSummary:
    pressers = data["pressers"]
    totals = compute_totals(pressers)
    ratings = [p["style"]["rating"] for p in pressers if p["style"] is not None]
    style = sum(ratings) / len(ratings) if ratings else None
    judged = all(p["faithfulness"] is not None for p in pressers)
    passes = (
        bool(pressers)
        and totals["errored"] == 0
        and judged
        and len(ratings) == len(pressers)
        and totals["faithfulness"] is not None
        and totals["faithfulness"] >= FAITHFULNESS_THRESHOLD - EPSILON
        and style is not None
        and style >= STYLE_THRESHOLD - EPSILON
    )
    return RunSummary(
        model=data["model"],
        pressers=totals["pressers"],
        errored=totals["errored"],
        faithfulness=totals["faithfulness"],
        style=style,
        rated=len(ratings),
        within_limit_share=totals["within_limit_share"],
        avg_cost_usd=totals["avg_cost_usd"],
        avg_latency_seconds=totals["avg_latency_seconds"],
        judge_agreement=data["totals"].get("judge_agreement"),
        passes=passes,
    )


def summarise(results_dir: Path) -> list[RunSummary]:
    runs = []
    for path in sorted(Path(results_dir).glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("split") == "test":
            runs.append(summarise_run(data))
    return sorted(runs, key=lambda run: run.model)


def choose(runs: Sequence[RunSummary]) -> str | None:
    candidates = [run for run in runs if run.passes and run.avg_cost_usd is not None]
    if not candidates:
        return None
    best = min(candidates, key=lambda run: (run.avg_cost_usd, -(run.faithfulness or 0.0)))
    return best.model


def format_run(run: RunSummary) -> str:
    def fmt(value: float | None, pattern: str = "{:.3f}") -> str:
        return "n/a" if value is None else pattern.format(value)

    return (
        f"{run.model}: pressers {run.pressers}  errored {run.errored}"
        f"  faithfulness {fmt(run.faithfulness)}  style {fmt(run.style, '{:.2f}')}"
        f" ({run.rated} rated)  within limit {fmt(run.within_limit_share)}"
        f"  cost {fmt(run.avg_cost_usd, '${:.6f}')}"
        f"  latency {fmt(run.avg_latency_seconds, '{:.1f}s')}"
        f"  judge agreement {fmt(run.judge_agreement, '{:.2f}')}"
        f"  {'PASS' if run.passes else 'fail'}"
    )
