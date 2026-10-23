import json
from pathlib import Path
from typing import Any

from app.core.eval_results import NO_HEADER, read_header
from app.retrieval.evaluation.dataset import EvaluationError

# Recall is only comparable when measured on the same set, split, labels and fusion constant.
MATCHING_KEYS = ("set_version", "split", "reference_judge", "k")


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def load_runs(paths: list[Path]) -> list[tuple[Path, dict[str, Any], dict[str, Any]]]:
    runs = []
    for path in paths:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise EvaluationError(f"cannot read the result file {path}") from None
        header = read_header(data)
        if header is None:
            raise EvaluationError(f"{path}: {NO_HEADER}; it cannot be compared")
        if "split" not in header or not isinstance(data.get("free"), dict):
            raise EvaluationError(f"{path}: not a result of the retrieval evaluation")
        runs.append((Path(path), data, header))
    first = runs[0][2]
    for path, _, header in runs[1:]:
        for key in MATCHING_KEYS:
            if header.get(key) != first.get(key):
                raise EvaluationError(
                    f"{path}: {key} {header.get(key)!r} differs from {first.get(key)!r}"
                    f" of {runs[0][0]}; results measured against different sets, splits,"
                    " labels or fusion settings are never mixed"
                )
    return runs


def format_compare(runs: list[tuple[Path, dict[str, Any], dict[str, Any]]]) -> str:
    first = runs[0][2]
    lines = [f"set {first['set_version']}  split {first['split']}  files {len(runs)}"]
    for path, _, header in runs:
        depths = ",".join(str(depth) for depth in header.get("depths") or []) or "-"
        lines.append(f"  {path.name}  model {header['model']}  depths {depths}")
    player = [(h["model"], d["player"]["rows"]) for _, d, h in runs if d.get("player")]
    if any(rows for _, rows in player):
        lines.append("")
        lines.append(
            f"{'model':<34}{'mode':<10}{'variant':<11}{'depth':>6}{'recall@cut':>12}"
            f"{'recall@depth':>14}{'recall@10':>11}{'empty':>7}{'unjudged':>10}"
        )
        for model, rows in player:
            for row in rows:
                lines.append(
                    f"{model:<34}{row['mode']:<10}{row['variant']:<11}{row['depth']:>6}"
                    f"{_fmt(row['recall_at_cut']):>12}{_fmt(row.get('recall_at_depth')):>14}"
                    f"{_fmt(row['recall_at_10']):>11}"
                    f"{row['empty_cuts_with_miss']:>7}{_fmt(row['unjudged_share']):>10}"
                )
    lines.append("")
    lines.append(
        f"{'model':<34}{'mode':<10}{'slice':<7}{'n':>4}{'recall@5':>10}{'recall@10':>11}"
        f"{'MRR':>8}{'unjudged':>10}"
    )
    for _, data, header in runs:
        for mode, slices in data["free"]["modes"].items():
            for name, m in slices.items():
                lines.append(
                    f"{header['model']:<34}{mode:<10}{name:<7}{m['n']:>4}"
                    f"{_fmt(m['recall_at_5']):>10}{_fmt(m['recall_at_10']):>11}"
                    f"{_fmt(m['mrr']):>8}{_fmt(m.get('unjudged_share')):>10}"
                )
    return "\n".join(lines)
