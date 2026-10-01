import pytest
from typer.testing import CliRunner

from app.corroboration.evaluation.cases import load_cases, write_cases
from app.corroboration.evaluation.cli import app
from tests.corroboration.evaluation.test_cases import make_case


def _files(tmp_path, cases):
    path = tmp_path / "cases.jsonl"
    write_cases(path, cases)
    return path


def _review(path, *args, input_text=""):
    return CliRunner().invoke(app, ["review", "--cases", str(path), *args], input=input_text)


def _cases():
    return [
        make_case(1, "supports", split="dev"),
        make_case(2, "related", split="test"),
        make_case(3, "unrelated", split="test"),
        make_case(4, "contradicts", split="test", reviewed=True),
    ]


def test_accept_marks_the_case_reviewed_on_disk_after_the_decision(tmp_path):
    path = _files(tmp_path, _cases())
    result = _review(path, input_text="a\nq\n")
    assert result.exit_code == 0, result.output
    by_id = {c.id: c for c in load_cases(path)}
    assert by_id["jc-001"].reviewed is True
    assert by_id["jc-001"].expected == "supports"
    assert by_id["jc-002"].reviewed is False
    assert "accepted: 1" in result.stdout


def test_change_asks_for_a_label_and_saves_it_reviewed(tmp_path):
    path = _files(tmp_path, _cases())
    result = _review(path, input_text="c\ncontradicts\nq\n")
    assert result.exit_code == 0, result.output
    changed = {c.id: c for c in load_cases(path)}["jc-001"]
    assert (changed.expected, changed.reviewed) == ("contradicts", True)
    assert changed.labelled_by == "anthropic/claude-haiku-4.5"
    assert "changed: 1" in result.stdout


def test_change_rejects_an_unknown_label_and_asks_again(tmp_path):
    path = _files(tmp_path, _cases())
    result = _review(path, input_text="c\nmaybe\nrelated\nq\n")
    assert result.exit_code == 0, result.output
    assert {c.id: c for c in load_cases(path)}["jc-001"].expected == "related"
    assert "supports, contradicts, related or unrelated" in result.output


def test_skip_leaves_the_case_untouched_and_moves_on(tmp_path):
    path = _files(tmp_path, _cases())
    before = path.read_text()
    result = _review(path, input_text="s\ns\ns\n")
    assert result.exit_code == 0, result.output
    assert path.read_text() == before
    assert "skipped: 3" in result.stdout


def test_quit_stops_and_keeps_the_earlier_decisions(tmp_path):
    path = _files(tmp_path, _cases())
    result = _review(path, input_text="a\na\nq\n")
    assert result.exit_code == 0, result.output
    reviewed = {c.id for c in load_cases(path) if c.reviewed}
    assert reviewed == {"jc-001", "jc-002", "jc-004"}


def test_split_and_id_select(tmp_path):
    path = _files(tmp_path, _cases())
    result = _review(path, "--split", "test", input_text="a\na\n")
    assert result.exit_code == 0, result.output
    assert {c.id for c in load_cases(path) if c.reviewed} == {"jc-002", "jc-003", "jc-004"}

    path = _files(tmp_path, _cases())
    result = _review(path, "--id", "jc-004", input_text="c\nsupports\n")
    assert result.exit_code == 0, result.output
    assert {c.id: c for c in load_cases(path)}["jc-004"].expected == "supports"


def test_an_unknown_id_or_split_exits_1(tmp_path):
    path = _files(tmp_path, _cases())
    assert _review(path, "--id", "nope").exit_code == 1
    assert _review(path, "--split", "prod").exit_code == 1


def test_nothing_to_review(tmp_path):
    path = _files(tmp_path, [make_case(1, reviewed=True)])
    result = _review(path)
    assert result.exit_code == 0
    assert "nothing to review" in result.stdout


def test_the_case_is_rendered_with_player_anchor_post_and_prelabel(tmp_path):
    path = _files(tmp_path, _cases())
    result = _review(path, input_text="q\n")
    out = result.stdout
    assert "Isak" in out and "Newcastle" in out
    assert "doubt" in out and "likely" in out and "Isak doubtful" in out
    assert "@lister" in out and "post 1" in out
    assert "repost of @origin" in out
    assert "pre-label: supports" in out
    assert "anthropic/claude-haiku-4.5" in out
    assert "left in this run" in out


def test_an_unknown_action_is_reported_and_asked_again(tmp_path):
    path = _files(tmp_path, _cases())
    result = _review(path, input_text="z\nq\n")
    assert "unknown action" in result.output


@pytest.mark.parametrize("input_text", ["a\n", "a\nc\n"])
def test_an_interrupted_review_exits_130_with_the_decisions_saved(tmp_path, input_text):
    path = _files(tmp_path, _cases())
    result = _review(path, input_text=input_text)
    assert result.exit_code == 130
    assert {c.id: c for c in load_cases(path)}["jc-001"].reviewed is True
    assert "reviewed:" in result.stdout
