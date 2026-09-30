import json
import os
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from app.corroboration.evaluation.cases import DEFAULT_RESULTS_DIR, JudgeCase
from app.corroboration.evaluation.metrics import Outcome, compute_metrics
from app.corroboration.judge import Judge, JudgeInput
from app.corroboration.schemas import Claim, PlayerRef, PostRef


class EvaluationError(Exception):
    pass


def default_result_path(split: str, model: str) -> Path:
    return DEFAULT_RESULTS_DIR / f"{split}-{model.replace('/', '-')}.json"


def _judge_input(case: JudgeCase) -> JudgeInput:
    anchor, post = case.anchor, case.post
    return JudgeInput(
        player=PlayerRef(
            season="",
            fpl_id=case.player.fpl_id,
            web_name=case.player.web_name,
            team_name=case.player.team,
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
    for case in chosen:
        try:
            reply = judge.run(_judge_input(case))
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
    return {
        "model": model,
        "prompt_version": prompt_version,
        "split": split,
        "date": now.isoformat(),
        "include_unreviewed": include_unreviewed,
        "cost_usd": sum(costs) if costs else None,
        "metrics": compute_metrics(outcomes),
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


def write_result(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def format_metrics(data: dict[str, Any]) -> str:
    metrics = data["metrics"]

    def fmt(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.3f}"

    lines = [
        f"model: {data['model']}  prompt: {data['prompt_version']}  split: {data['split']}",
        f"cases: {metrics['cases']}  errored: {metrics['errored']}",
        f"accuracy: {fmt(metrics['accuracy'])}",
    ]
    for label, row in metrics["per_label"].items():
        lines.append(
            f"{label}: precision {fmt(row['precision'])}  recall {fmt(row['recall'])}"
            f"  (expected {row['expected']}, predicted {row['predicted']})"
        )
    fs = metrics["false_support"]
    lines.append(f"false-support rate: {fmt(fs['rate'])} ({fs['numerator']}/{fs['denominator']})")
    cost = data["cost_usd"]
    lines.append(f"cost: {'n/a' if cost is None else f'${cost:.6f}'}")
    return "\n".join(lines)
