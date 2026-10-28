from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.eval_results import write_json_atomic
from app.corroboration.evaluation.cases import (
    DEFAULT_RESULTS_DIR,
    CaseAnchor,
    CasePlayer,
    CasePost,
    JudgeCase,
)
from app.corroboration.evaluation.dataset import load_manifest
from app.corroboration.evaluation.metrics import Outcome, compute_metrics, threshold_verdict
from app.corroboration.judge import Judge, JudgeInput
from app.corroboration.schemas import Claim, PlayerRef, PostRef


class EvaluationError(Exception):
    pass


def default_result_path(split: str, model: str, set_version: str = "v1") -> Path:
    name = f"{split}-{model.replace('/', '-')}.json"
    if set_version == "v1":
        return DEFAULT_RESULTS_DIR / name
    if split == "dev":
        return DEFAULT_RESULTS_DIR / set_version / "dev" / name
    return DEFAULT_RESULTS_DIR / set_version / name


def judge_input(player: CasePlayer, anchor: CaseAnchor, post: CasePost) -> JudgeInput:
    return JudgeInput(
        player=PlayerRef(
            season="", fpl_id=player.fpl_id, web_name=player.web_name, team_name=player.team
        ),
        anchor=Claim(
            post=PostRef(
                anchor.x_id, anchor.author_handle, None, False, anchor.created_at, anchor.text
            ),
            event_type=anchor.event_type,
            certainty=anchor.certainty,
        ),
        post=PostRef(
            post.x_id,
            post.author_handle,
            post.reposted_author_handle,
            post.is_repost,
            post.created_at,
            post.text,
        ),
    )


def run_evaluation(
    cases: Sequence[JudgeCase],
    judge: Judge,
    split: str,
    include_unreviewed: bool,
    model: str,
    prompt_version: str,
    now: datetime,
    calls_per_month: float | None = None,
    usd_pln_rate: float | None = None,
) -> dict[str, Any]:
    in_split = [case for case in cases if case.split == split]
    chosen = [case for case in in_split if case.reviewed or include_unreviewed]
    if not chosen:
        if in_split:
            raise EvaluationError(
                f"no reviewed cases in the {split} split; run review or pass --include-unreviewed"
            )
        raise EvaluationError(f"the {split} split has no cases")

    outcomes: list[Outcome] = []
    production_ids = {case.id for case in chosen if not case.has_player_event}
    for case in chosen:
        try:
            reply = judge.run(judge_input(case.player, case.anchor, case.post))
        except Exception as exc:
            outcomes.append(Outcome(case.id, case.expected, None, error_class=type(exc).__name__))
            continue
        outcomes.append(
            Outcome(
                case.id,
                case.expected,
                reply.parsed.label,
                input_tokens=reply.usage.input_tokens,
                output_tokens=reply.usage.output_tokens,
                cost_usd=reply.cost_usd,
            )
        )
    costs = [o.cost_usd for o in outcomes if o.cost_usd is not None]
    metrics = compute_metrics(outcomes)
    projected = None
    if costs and calls_per_month is not None and usd_pln_rate is not None:
        projected = sum(costs) / len(costs) * calls_per_month * usd_pln_rate
    return {
        "model": model,
        "prompt_version": prompt_version,
        "split": split,
        "date": now.isoformat(),
        "include_unreviewed": include_unreviewed,
        "cost_usd": sum(costs) if costs else None,
        "metrics": metrics,
        "production": compute_metrics([o for o in outcomes if o.id in production_ids]),
        "verdict": threshold_verdict(metrics),
        "projected_monthly_cost_pln": projected,
        "outcomes": [
            {
                "id": o.id,
                "expected": o.expected,
                "predicted": o.predicted,
                "error_class": o.error_class,
                "input_tokens": o.input_tokens,
                "output_tokens": o.output_tokens,
                "cost_usd": o.cost_usd,
            }
            for o in outcomes
        ],
    }


def calls_per_month(set_dir: Path) -> float | None:
    manifest = load_manifest(set_dir)
    return manifest.build.judge_calls_per_month if manifest and manifest.build else None


def write_result(path: Path, data: dict[str, Any]) -> None:
    write_json_atomic(path, data)


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _metric_lines(metrics: dict[str, Any]) -> list[str]:
    lines = [
        f"cases: {metrics['cases']}  errored: {metrics['errored']}",
        f"accuracy: {_fmt(metrics['accuracy'])}",
    ]
    for label, row in metrics["per_label"].items():
        lines.append(
            f"{label}: precision {_fmt(row['precision'])}  recall {_fmt(row['recall'])}"
            f"  (expected {row['expected']}, predicted {row['predicted']})"
        )
    fs = metrics["false_support"]
    lines.append(f"false-support rate: {_fmt(fs['rate'])} ({fs['numerator']}/{fs['denominator']})")
    return lines


def format_metrics(data: dict[str, Any]) -> str:
    lines = [f"model: {data['model']}  prompt: {data['prompt_version']}  split: {data['split']}"]
    lines += _metric_lines(data["metrics"])
    lines.append("production slice (has_player_event: false)")
    lines += [f"  {line}" for line in _metric_lines(data["production"])]
    verdict = data["verdict"]
    for name in ("accuracy", "false_support", "contradicts_recall", "no_errors"):
        lines.append(f"threshold {name}: {'pass' if verdict[name] else 'fail'}")
    lines.append(f"verdict: {'PASS' if verdict['passed'] else 'FAIL'}")
    cost = data["cost_usd"]
    lines.append(f"cost: {'n/a' if cost is None else f'${cost:.6f}'}")
    monthly = data.get("projected_monthly_cost_pln")
    lines.append(f"projected cost a month: {'n/a' if monthly is None else f'{monthly:.2f} PLN'}")
    return "\n".join(lines)
