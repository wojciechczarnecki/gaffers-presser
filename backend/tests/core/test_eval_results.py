import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.eval_results import (
    ResultExistsError,
    check_new_result,
    git_state,
    make_header,
    read_header,
    write_json_atomic,
    write_lines_atomic,
)

GIT = ["git", "-c", "user.name=test", "-c", "user.email=test@example.com"]
GIT += ["-c", "commit.gpgsign=false"]
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
BACKEND = Path(__file__).resolve().parents[2]


def test_make_header_has_the_eight_keys_and_extras():
    header = make_header(
        set_version="v2",
        model="m",
        prompt_version=None,
        reasoning_effort=None,
        judge=None,
        now=NOW,
        extra={"split": "dev"},
    )
    assert set(header) == {
        "set_version",
        "model",
        "prompt_version",
        "reasoning_effort",
        "judge",
        "date",
        "git_commit",
        "git_dirty",
        "split",
    }
    assert header["prompt_version"] is None
    assert header["date"] == NOW.isoformat()
    assert header["split"] == "dev"


def test_git_state_reports_commit_and_dirty_flag(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "a.txt").write_text("one")
    subprocess.run([*GIT, "-C", str(tmp_path), "add", "a.txt"], check=True)
    subprocess.run([*GIT, "-C", str(tmp_path), "commit", "-qm", "init"], check=True)
    commit, dirty = git_state(tmp_path)
    assert commit and len(commit) == 40
    assert dirty is False
    (tmp_path / "a.txt").write_text("two")
    assert git_state(tmp_path) == (commit, True)


def test_git_state_outside_a_repository_is_none(tmp_path):
    assert git_state(tmp_path) == (None, None)


def test_read_header_is_none_without_header():
    assert read_header({"metrics": {}}) is None
    assert read_header({"header": {"set_version": "v1"}}) == {"set_version": "v1"}


def test_write_json_atomic_keeps_old_content_and_leaves_no_tmp(tmp_path):
    path = tmp_path / "out" / "r.json"
    write_json_atomic(path, {"a": 1})
    with pytest.raises(TypeError):
        write_json_atomic(path, {"a": object()})
    assert json.loads(path.read_text()) == {"a": 1}
    assert [p.name for p in path.parent.iterdir()] == ["r.json"]


def test_write_lines_atomic(tmp_path):
    path = tmp_path / "x.jsonl"
    write_lines_atomic(path, ["a", "b"])
    assert path.read_text() == "a\nb\n"
    write_lines_atomic(path, [])
    assert path.read_text() == ""


def test_check_new_result_refuses_existing_unless_forced(tmp_path):
    path = tmp_path / "r.json"
    check_new_result(path, False)
    path.write_text("{}")
    with pytest.raises(ResultExistsError, match=str(path)):
        check_new_result(path, False)
    check_new_result(path, True)


def test_committed_result_files_load_with_or_without_a_header():
    # Only committed files: a local `--split dev` run lands in a gitignored directory.
    listed = subprocess.run(
        ["git", "ls-files", "evals/*/results/*.json", "evals/*/results/**/*.json"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert listed
    for name in listed:
        header = read_header(json.loads((BACKEND / name).read_text()))
        assert header is None or isinstance(header, dict), name
