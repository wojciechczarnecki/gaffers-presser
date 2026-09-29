import json
import shlex
import sys
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from app.extraction.cli import app
from app.extraction.evaluation.cases import EvalCase, ExpectedEvent, load_cases, write_cases

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
# A season no alias in aliases.toml points at, so the real aliases never reach these players.
SEASON = "test"

EDITOR_STUB = """
import json, os, sys
queue_path = os.environ["STUB_QUEUE"]
queue = json.loads(open(queue_path).read())
with open(os.environ["STUB_SEEN"], "a") as seen:
    seen.write(json.dumps(open(sys.argv[1]).read()) + "\\n")
text = queue.pop(0)
open(queue_path, "w").write(json.dumps(queue))
open(sys.argv[1], "w").write(text)
"""


def _player(fpl_id, web_name, first_name, second_name, team_fpl_id):
    return {
        "season": SEASON,
        "fpl_id": fpl_id,
        "web_name": web_name,
        "first_name": first_name,
        "second_name": second_name,
        "team_fpl_id": team_fpl_id,
    }


def _event(mention="Haaland", fpl_id=5, event_type="out", certainty="confirmed"):
    return ExpectedEvent(mention=mention, fpl_id=fpl_id, event_type=event_type, certainty=certainty)


def _case(case_id, split="test", reviewed=False, events=None, **overrides) -> EvalCase:
    fields = dict(
        id=case_id,
        author_handle="reporter",
        text=f"Post {case_id}\nsecond line",
        created_at=NOW,
        is_repost=False,
        is_reply=False,
        split=split,
        synthetic=False,
        reviewed=reviewed,
        tags=[],
        expected_events=[_event()] if events is None else events,
    )
    fields.update(overrides)
    return EvalCase(**fields)


class Files:
    def __init__(self, tmp_path, cases):
        self.tmp_path = tmp_path
        self.cases = tmp_path / "cases.jsonl"
        self.players = tmp_path / "players.json"
        write_cases(self.cases, cases)
        self.players.write_text(
            json.dumps(
                {
                    "season": SEASON,
                    "players": [
                        _player(5, "Haaland", "Erling", "Haaland", 1),
                        _player(9, "Hincapie", "Piero", "Hincapié", 2),
                        _player(4, "Gabriel", "Gabriel", "dos Santos Magalhães", 2),
                    ],
                    "teams": [
                        {"season": SEASON, "fpl_id": 1, "name": "Man City", "short_name": "MCI"},
                        {"season": SEASON, "fpl_id": 2, "name": "Arsenal", "short_name": "ARS"},
                    ],
                }
            )
        )

    def run(self, *actions, extra=()):
        args = ["review", "--cases", str(self.cases), "--players", str(self.players), *extra]
        return CliRunner().invoke(app, args, input="".join(f"{a}\n" for a in actions))

    def loaded(self):
        return load_cases(self.cases)


@pytest.fixture
def editor(tmp_path, monkeypatch):
    """Replaces $EDITOR with a stub that saves the queued texts, one per editor launch."""
    stub = tmp_path / "editor_stub.py"
    stub.write_text(EDITOR_STUB)
    queue = tmp_path / "queue.json"
    seen = tmp_path / "seen.jsonl"
    seen.write_text("")
    monkeypatch.setenv("EDITOR", f"{shlex.quote(sys.executable)} {shlex.quote(str(stub))}")
    monkeypatch.setenv("STUB_QUEUE", str(queue))
    monkeypatch.setenv("STUB_SEEN", str(seen))

    class Editor:
        def will_save(self, *texts):
            queue.write_text(json.dumps(list(texts)))

        def seen(self):
            return [json.loads(line) for line in seen.read_text().splitlines()]

    return Editor()


def _edited_json(case: EvalCase, **changes) -> str:
    data = case.model_dump(mode="json")
    data.update(changes)
    return json.dumps(data)


def test_accept_sets_reviewed_and_saves(tmp_path):
    files = Files(tmp_path, [_case("1"), _case("2")])
    result = files.run("a", "q")
    assert result.exit_code == 0, result.output
    assert [c.reviewed for c in files.loaded()] == [True, False]
    assert "accepted: 1  edited: 0  skipped: 0" in result.output
    assert "reviewed: 1/2" in result.output


def test_skip_changes_nothing(tmp_path):
    files = Files(tmp_path, [_case("1"), _case("2")])
    before = files.cases.read_bytes()
    result = files.run("s", "s")
    assert result.exit_code == 0, result.output
    assert files.cases.read_bytes() == before
    assert "skipped: 2" in result.output


def test_quit_midway_keeps_earlier_accepts_and_the_rest_untouched(tmp_path):
    cases = [_case("1"), _case("2", split="dev"), _case("3", reviewed=True), _case("4")]
    files = Files(tmp_path, cases)
    result = files.run("a", "q")
    assert result.exit_code == 0, result.output
    loaded = files.loaded()
    assert [c.id for c in loaded] == ["1", "2", "3", "4"]
    assert loaded[0] == cases[0].model_copy(update={"reviewed": True})
    assert loaded[1:] == cases[1:]


def test_interrupt_keeps_accepted_cases(tmp_path):
    files = Files(tmp_path, [_case("1"), _case("2")])
    result = files.run("a")  # input ends on the next prompt, as Ctrl-C would
    assert result.exit_code == 130
    assert [c.reviewed for c in files.loaded()] == [True, False]
    assert "reviewed: 1/2" in result.output


def test_resume_starts_at_the_first_unreviewed(tmp_path):
    files = Files(tmp_path, [_case("1", reviewed=True), _case("2"), _case("3")])
    result = files.run("q")
    assert "id: 2 " in result.output
    assert "id: 1 " not in result.output
    assert "1/3 reviewed, 2 left in this run" in result.output


def test_split_filters(tmp_path):
    files = Files(tmp_path, [_case("1", split="test"), _case("2", split="dev")])
    result = files.run("a", extra=["--split", "dev"])
    assert result.exit_code == 0, result.output
    assert "id: 1 " not in result.output
    assert [c.reviewed for c in files.loaded()] == [False, True]


def test_invalid_split_is_refused(tmp_path):
    files = Files(tmp_path, [_case("1")])
    result = files.run(extra=["--split", "train"])
    assert result.exit_code == 1
    assert "--split must be dev or test" in result.output


def test_id_reopens_a_reviewed_case(tmp_path):
    files = Files(tmp_path, [_case("1"), _case("2", reviewed=True)])
    result = files.run("s", extra=["--id", "2"])
    assert result.exit_code == 0, result.output
    assert "id: 2 " in result.output
    assert "id: 1 " not in result.output


def test_unknown_id_fails(tmp_path):
    files = Files(tmp_path, [_case("1")])
    result = files.run(extra=["--id", "404"])
    assert result.exit_code == 1
    assert "no case with id 404" in result.output


def test_nothing_to_review(tmp_path):
    files = Files(tmp_path, [_case("1", reviewed=True)])
    result = files.run()
    assert result.exit_code == 0
    assert "nothing to review" in result.output


def test_display_shows_players_and_flags_unknown_ids(tmp_path):
    events = [_event(), _event("Nobody", 999), _event("Someone", None, "doubt", "rumour")]
    files = Files(
        tmp_path, [_case("1", events=events, tags=["multi_player"]), _case("2", events=[])]
    )
    result = files.run("s", "s")
    assert '1. "Haaland" -> 5 Haaland (Erling Haaland, MCI)  out / confirmed' in result.output
    assert '2. "Nobody" -> 999 !! NOT IN SNAPSHOT !!' in result.output
    assert '3. "Someone" -> unlinked (fpl_id null)  doubt / rumour' in result.output
    assert "tags: multi_player" in result.output
    assert "  second line" in result.output
    assert "expected events: no events" in result.output


def test_find_accented_player_by_an_unaccented_name(tmp_path):
    files = Files(tmp_path, [_case("1")])
    before = files.cases.read_bytes()
    result = files.run("f", "Hincapie", "", "f", "magalhaes", "ars", "f", "zzz", "", "q")
    assert result.exit_code == 0, result.output
    assert "9  Hincapie (Piero Hincapié, ARS)" in result.output
    assert "4  Gabriel (Gabriel dos Santos Magalhães, ARS)" in result.output
    assert "no players found" in result.output
    assert files.cases.read_bytes() == before


def test_find_falls_back_to_an_accent_free_substring(tmp_path):
    files = Files(tmp_path, [_case("1")])
    result = files.run("f", "hincap", "", "q")
    assert "9  Hincapie (Piero Hincapié, ARS)" in result.output
    assert "Haaland (" not in result.output.split("player name")[-1]


def test_edit_with_valid_json_changes_labels(tmp_path, editor):
    case = _case("1")
    files = Files(tmp_path, [case, _case("2")])
    new_events = [
        {"mention": "Hincapié", "fpl_id": 9, "event_type": "doubt", "certainty": "rumour"}
    ]
    editor.will_save(_edited_json(case, expected_events=new_events))
    result = files.run("e", "a", "q")
    assert result.exit_code == 0, result.output
    assert json.loads(editor.seen()[0]) == case.model_dump(mode="json")
    assert editor.seen()[0].startswith("{\n  ")  # formatted for editing
    loaded = files.loaded()
    assert loaded[0].expected_events == [_event("Hincapié", 9, "doubt", "rumour")]
    assert loaded[0].reviewed is True
    assert loaded[1] == _case("2")
    # the edited case is shown again before it is accepted
    assert '"Hincapié" -> 9 Hincapie (Piero Hincapié, ARS)' in result.output
    assert "accepted: 1  edited: 1  skipped: 0" in result.output


def test_edit_with_invalid_json_saves_nothing(tmp_path, editor):
    files = Files(tmp_path, [_case("1")])
    before = files.cases.read_bytes()
    editor.will_save("{not json", '{"still": "wrong"}')
    result = files.run("e", "r", "c", "q")
    assert result.exit_code == 0, result.output
    assert files.cases.read_bytes() == before
    assert result.output.count("not saved:") == 2
    assert "edit cancelled, nothing saved" in result.output
    assert editor.seen()[1] == "{not json"  # a retry reopens what was typed


def test_edit_changing_the_id_saves_nothing(tmp_path, editor):
    case = _case("1")
    files = Files(tmp_path, [case])
    before = files.cases.read_bytes()
    editor.will_save(_edited_json(case, id="2", reviewed=True))
    result = files.run("e", "c", "q")
    assert result.exit_code == 0, result.output
    assert files.cases.read_bytes() == before
    assert "the case id must stay '1'" in result.output
