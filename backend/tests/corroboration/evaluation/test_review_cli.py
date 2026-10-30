from typer.testing import CliRunner

from app.corroboration.evaluation.cases import (
    label_table_row,
    load_cases,
    render_case,
    write_cases,
)
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


def _v2(n, expected, prelabel, v1_prelabel=None, reviewed=False):
    return make_case(n, expected, reviewed=reviewed).model_copy(
        update={
            "prelabel": prelabel,
            "v1_prelabel": v1_prelabel,
            "labelled_by": "claude-opus-5-5 (Claude Code session)",
        }
    )


def test_a_key_saves_the_label_reviewed_on_disk_at_once(tmp_path):
    path = _files(tmp_path, _cases())
    result = _review(path, input_text="1\nq\n")
    assert result.exit_code == 0, result.output
    by_id = {c.id: c for c in load_cases(path)}
    assert (by_id["jc-001"].reviewed, by_id["jc-001"].expected) == (True, "supports")
    assert by_id["jc-002"].reviewed is False
    assert "accepted: 1" in result.stdout


def test_a_pick_that_differs_from_the_prelabel_is_saved_as_changed(tmp_path):
    path = _files(tmp_path, [_v2(1, "supports", "supports", v1_prelabel="supports")])
    result = _review(path, input_text="2\n")
    assert result.exit_code == 0, result.output
    case = load_cases(path)[0]
    assert (case.expected, case.reviewed) == ("contradicts", True)
    assert "changed: 1" in result.stdout


def test_a_accepts_the_session_prelabel(tmp_path):
    path = _files(tmp_path, [_v2(1, "supports", "related", v1_prelabel="related")])
    result = _review(path, input_text="a\n")
    assert result.exit_code == 0, result.output
    case = load_cases(path)[0]
    assert (case.expected, case.reviewed) == ("related", True)
    assert "accepted: 1" in result.stdout


def test_an_empty_enter_accepts_the_session_prelabel(tmp_path):
    path = _files(tmp_path, [_v2(1, "supports", "unrelated")])
    result = _review(path, input_text="\n")
    assert result.exit_code == 0, result.output
    assert load_cases(path)[0].expected == "unrelated"


def test_accept_is_refused_when_the_prelabels_disagree(tmp_path):
    path = _files(tmp_path, [_v2(1, "supports", "supports", v1_prelabel="related")])
    result = _review(path, input_text="a\n\n3\n")
    assert result.exit_code == 0, result.output
    assert result.stdout.count("accept is refused") == 2
    case = load_cases(path)[0]
    assert (case.expected, case.reviewed) == ("related", True)


def test_an_unknown_key_is_reported_and_asked_again(tmp_path):
    path = _files(tmp_path, _cases())
    result = _review(path, input_text="z\nq\n")
    assert "unknown key" in result.output


def test_back_returns_to_the_previous_case_to_redo_it(tmp_path):
    path = _files(tmp_path, [_v2(1, "supports", "supports"), _v2(2, "related", "related")])
    result = _review(path, input_text="1\nb\n1\n3\n")
    assert result.exit_code == 0, result.output
    assert result.stdout.count("id: jc-001") == 2
    assert {c.id: c.expected for c in load_cases(path)} == {
        "jc-001": "supports",
        "jc-002": "related",
    }


def test_back_on_the_first_case_stays_there(tmp_path):
    path = _files(tmp_path, [_v2(1, "supports", "supports")])
    result = _review(path, input_text="b\nq\n")
    assert result.stdout.count("id: jc-001") == 2


def test_skip_leaves_the_case_untouched_and_moves_on(tmp_path):
    path = _files(tmp_path, _cases())
    before = path.read_text()
    result = _review(path, input_text="s\ns\ns\n")
    assert result.exit_code == 0, result.output
    assert path.read_text() == before
    assert "skipped: 3" in result.stdout


def test_quit_stops_and_keeps_the_earlier_decisions(tmp_path):
    path = _files(tmp_path, _cases())
    result = _review(path, input_text="1\n3\nq\n")
    assert result.exit_code == 0, result.output
    assert {c.id for c in load_cases(path) if c.reviewed} == {"jc-001", "jc-002", "jc-004"}


def test_split_and_id_select(tmp_path):
    path = _files(tmp_path, _cases())
    result = _review(path, "--split", "test", input_text="3\n4\n")
    assert result.exit_code == 0, result.output
    assert {c.id for c in load_cases(path) if c.reviewed} == {"jc-002", "jc-003", "jc-004"}

    path = _files(tmp_path, _cases())
    result = _review(path, "--id", "jc-004", input_text="1\n")
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


def test_the_post_comes_first_then_the_anchor_then_the_label_row(tmp_path):
    path = _files(tmp_path, _cases())
    out = _review(path, input_text="q\n").stdout
    assert "LABEL THIS POST — what does it say about Isak (Newcastle)?" in out
    assert "@lister" in out and "post 1" in out and "repost of @origin" in out
    assert "COMPARE WITH (anchor): Isak is a DOUBT" in out
    assert "Isak doubtful" in out and "@anchor_acc" in out
    assert out.index("LABEL THIS POST") < out.index("post 1") < out.index("COMPARE WITH")
    assert out.index("COMPARE WITH") < out.index("Isak doubtful") < out.index("HOW TO LABEL")
    assert "If the anchor is doubt: post says doubt -> supports" in out
    assert "starts or is fit -> contradicts" in out
    assert "only for the next Premier League match" in " ".join(out.split())
    assert "Only the named player counts" in out
    assert "left in this run" in out


def test_prelabels_are_shown_below_the_label_row_and_marked_on_disagreement(tmp_path):
    path = _files(tmp_path, [_v2(3, "supports", "supports", v1_prelabel="unrelated")])
    out = _review(path, input_text="q\n").stdout
    assert "session pre-label: supports" in out
    assert "haiku pre-label: unrelated" in out
    assert "DISAGREE" in out
    assert out.index("HOW TO LABEL") < out.index("session pre-label")

    path = _files(tmp_path, [_v2(3, "supports", "supports", v1_prelabel="supports")])
    assert "DISAGREE" not in _review(path, input_text="q\n").stdout


def test_the_label_row_follows_the_anchor_event_type():
    for event_type in ("out", "doubt", "benched", "confirmed_starter"):
        assert f"If the anchor is {event_type}:" in label_table_row(event_type)


def test_long_texts_wrap_to_the_width_and_the_anchor_is_shortened():
    case = make_case(1)
    long_anchor = case.anchor.model_copy(update={"text": "word " * 200})
    long_post = case.post.model_copy(update={"text": "text " * 100})
    case = case.model_copy(update={"anchor": long_anchor, "post": long_post})
    out = render_case(case, "0/1", width=60)
    assert all(len(line) <= 60 for line in out.splitlines())
    assert "..." in out
    assert "\n\n" in out


def test_review_shows_disagreements_first(tmp_path):
    cases = [
        _v2(1, "supports", "supports"),
        _v2(2, "related", "related", v1_prelabel="related"),
        _v2(3, "supports", "supports", v1_prelabel="unrelated"),
        _v2(4, "related", "related", v1_prelabel="supports", reviewed=True),
    ]
    path = _files(tmp_path, cases)
    result = _review(path, input_text="1\n3\n1\n")
    assert result.exit_code == 0, result.output
    positions = [result.stdout.index(f"id: jc-00{n}") for n in (3, 2, 1)]
    assert positions == sorted(positions)
    assert "id: jc-004" not in result.stdout


def test_agreement_counts_differences_per_prelabeller(tmp_path):
    cases = [
        _v2(1, "supports", "supports", reviewed=True),
        _v2(2, "contradicts", "supports", reviewed=True),
        _v2(3, "related", "related", v1_prelabel="supports", reviewed=True),
        _v2(4, "supports", "supports", v1_prelabel="supports", reviewed=True),
        _v2(5, "supports", "related"),
    ]
    path = _files(tmp_path, cases)
    result = CliRunner().invoke(app, ["agreement", "--cases", str(path)])
    assert result.exit_code == 0, result.output
    assert "reviewed: 4" in result.stdout
    assert "claude-opus-5-5 (Claude Code session): 1 of 4 differ" in result.stdout
    assert "anthropic/claude-haiku-4.5: 1 of 2 differ" in result.stdout
    assert "jc-002: owner contradicts, session supports" in result.stdout
    assert "jc-005" not in result.stdout
