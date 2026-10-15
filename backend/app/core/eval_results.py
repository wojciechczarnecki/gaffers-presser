import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.errors import AppError

NO_HEADER = "no header"
REPO_ROOT = Path(__file__).resolve().parents[3]


class ResultExistsError(AppError):
    pass


def _git(cwd: Path, *args: str) -> str | None:
    try:
        done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    except OSError:
        return None
    return done.stdout if done.returncode == 0 else None


def git_state(cwd: Path = REPO_ROOT) -> tuple[str | None, bool | None]:
    commit = _git(cwd, "rev-parse", "HEAD")
    status = _git(cwd, "status", "--porcelain")
    if commit is None or status is None:
        return None, None
    return commit.strip(), bool(status.strip())


def make_header(
    *,
    set_version: str | None,
    model: str | None,
    prompt_version: str | None,
    reasoning_effort: str | None,
    judge: str | None,
    now: datetime,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    commit, dirty = git_state()
    header: dict[str, Any] = {
        "set_version": set_version,
        "model": model,
        "prompt_version": prompt_version,
        "reasoning_effort": reasoning_effort,
        "judge": judge,
        "date": now.isoformat(),
        "git_commit": commit,
        "git_dirty": dirty,
    }
    header.update(extra or {})
    return header


def read_header(data: Mapping[str, Any]) -> dict[str, Any] | None:
    header = data.get("header")
    return dict(header) if isinstance(header, Mapping) else None


def write_text_atomic(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_lines_atomic(path: Path, lines: Sequence[str]) -> None:
    write_text_atomic(path, "\n".join(lines) + ("\n" if lines else ""))


def write_json_atomic(path: Path, data: Mapping[str, Any]) -> None:
    write_text_atomic(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def check_new_result(path: Path, force: bool) -> None:
    if Path(path).exists() and not force:
        raise ResultExistsError(f"{path} exists; pass --force to overwrite it")
