import json
import time
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.eval_results import write_json_atomic
from app.presser.evaluation.cases import DEFAULT_RESULTS_DIR, PresserCase
from app.presser.evaluation.judge import PROMPT_VERSION as JUDGE_PROMPT_VERSION
from app.presser.evaluation.judge import JudgeInput, JudgeVerdict, PresserJudge, render_input
from app.presser.writer import PROMPT_VERSION as WRITER_PROMPT_VERSION
from app.presser.writer import Writer, WriterInput

LENGTH_LIMIT = 2500


class EvaluationError(Exception):
    pass


def default_result_path(split: str, model: str, results_dir: Path = DEFAULT_RESULTS_DIR) -> Path:
    return results_dir / f"{split}-{model.replace('/', '-')}.json"


def _faithfulness(labels: Sequence[str]) -> float | None:
    # A non-empty presser with no claims was not judged; it must not score a perfect 1.0.
    if not labels:
        return None
    return sum(label == "supported" for label in labels) / len(labels)


def _average(values: Sequence[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else None


def compute_totals(pressers: Sequence[dict[str, Any]]) -> dict[str, Any]:
    written = [p for p in pressers if p["text"] is not None]
    judge_costs = [p["judge_cost_usd"] for p in pressers if p["judge_cost_usd"] is not None]
    return {
        "pressers": len(pressers),
        "errored": sum(p["error_class"] is not None for p in pressers),
        "faithfulness": _average([p["faithfulness"] for p in pressers]),
        "within_limit_share": (
            sum(p["within_limit"] for p in written) / len(written) if written else None
        ),
        "avg_cost_usd": _average([p["cost_usd"] for p in written]),
        "avg_latency_seconds": _average([p["latency_seconds"] for p in written]),
        "judge_cost_usd": sum(judge_costs) if judge_costs else None,
    }


def _record(case: PresserCase) -> dict[str, Any]:
    return {
        "case_id": case.id,
        "text": None,
        "length": None,
        "within_limit": None,
        "input_tokens": None,
        "output_tokens": None,
        "cost_usd": None,
        "latency_seconds": None,
        "claims": [],
        "faithfulness": None,
        "judge_cost_usd": None,
        "error_class": None,
        "style": None,
    }


def run_evaluation(
    cases: Sequence[PresserCase],
    writer: Writer,
    judge: PresserJudge | None,
    split: str,
    model: str,
    judge_model: str | None,
    now: datetime,
) -> dict[str, Any]:
    # judge None: the pressers are written only; `import_verdicts` adds the judge's labels later.
    chosen = [case for case in cases if case.split == split]
    if not chosen:
        raise EvaluationError(f"the {split} split has no cases")
    pressers = []
    for case in chosen:
        record = _record(case)
        pressers.append(record)
        started = time.monotonic()
        try:
            reply = writer.run(WriterInput(case.facts, case.previous))
        except Exception as exc:
            record["error_class"] = type(exc).__name__
            continue
        text = reply.parsed.text
        record.update(
            text=text,
            length=len(text),
            within_limit=len(text) <= LENGTH_LIMIT,
            input_tokens=reply.usage.input_tokens,
            output_tokens=reply.usage.output_tokens,
            cost_usd=reply.cost_usd,
            latency_seconds=time.monotonic() - started,
        )
        if judge is None:
            continue
        try:
            verdict = judge.run(JudgeInput(case.facts, case.previous, text))
        except Exception as exc:
            record["error_class"] = type(exc).__name__
            continue
        _apply_verdict(record, verdict.parsed)
        record["judge_cost_usd"] = verdict.cost_usd
    return {
        "model": model,
        "judge_model": judge_model,
        "prompt_version": WRITER_PROMPT_VERSION,
        "judge_prompt_version": JUDGE_PROMPT_VERSION,
        "split": split,
        "date": now.isoformat(),
        "pressers": pressers,
        "totals": compute_totals(pressers),
    }


def _apply_verdict(record: dict[str, Any], verdict: JudgeVerdict) -> None:
    record["claims"] = [
        {"claim": item.claim, "label": item.label, "owner_label": None} for item in verdict.claims
    ]
    record["faithfulness"] = _faithfulness([item.label for item in verdict.claims])


def judge_inputs(data: dict[str, Any], cases: Sequence[PresserCase]) -> dict[str, str]:
    """The judge's user message per case ID, exactly as the API judge would get it."""
    by_id = {case.id: case for case in cases}
    inputs = {}
    for record in data["pressers"]:
        if record["text"] is None:
            continue
        case = by_id.get(record["case_id"])
        if case is None:
            raise EvaluationError(f"case {record['case_id']} is not in the cases file")
        inputs[case.id] = render_input(JudgeInput(case.facts, case.previous, record["text"]))
    return inputs


def import_verdicts(
    data: dict[str, Any], verdicts: dict[str, JudgeVerdict], judge_model: str
) -> dict[str, Any]:
    """Records verdicts given outside the API (e.g. by a Claude Code session) in a run."""
    written = {p["case_id"] for p in data["pressers"] if p["text"] is not None}
    missing = sorted(written - verdicts.keys())
    if missing:
        raise EvaluationError(f"no verdict for: {', '.join(missing)}")
    unknown = sorted(verdicts.keys() - written)
    if unknown:
        raise EvaluationError(f"verdicts for cases without a presser: {', '.join(unknown)}")
    for record in data["pressers"]:
        if record["text"] is None:
            continue
        _apply_verdict(record, verdicts[record["case_id"]])
        record["judge_cost_usd"] = None
        if record["error_class"] is not None and record["faithfulness"] is not None:
            record["error_class"] = None  # the API judge's failure, now replaced
    data["judge_model"] = judge_model
    data["judge_prompt_version"] = JUDGE_PROMPT_VERSION
    if isinstance(data.get("header"), dict):
        data["header"]["judge"] = judge_model
    data["totals"] = compute_totals(data["pressers"])
    return data


def load_result(path: Path) -> dict[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise EvaluationError(f"cannot read the run file {path.name}") from None


def write_result(path: Path, data: dict[str, Any]) -> None:
    write_json_atomic(path, data)


def format_totals(data: dict[str, Any]) -> str:
    totals = data["totals"]

    def fmt(value: float | None, pattern: str = "{:.3f}") -> str:
        return "n/a" if value is None else pattern.format(value)

    return "\n".join(
        [
            f"model: {data['model']}  judge: {data['judge_model']}  split: {data['split']}",
            f"pressers: {totals['pressers']}  errored: {totals['errored']}",
            f"faithfulness: {fmt(totals['faithfulness'])}",
            f"within 2500 characters: {fmt(totals['within_limit_share'])}",
            f"avg cost: {fmt(totals['avg_cost_usd'], '${:.6f}')}"
            f"  avg latency: {fmt(totals['avg_latency_seconds'], '{:.1f}s')}",
            f"judge cost: {fmt(totals['judge_cost_usd'], '${:.6f}')}",
        ]
    )
