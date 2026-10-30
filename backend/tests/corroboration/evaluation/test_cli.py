import json

from typer.testing import CliRunner

from app.corroboration.evaluation.cases import load_cases, write_cases
from app.corroboration.evaluation.cli import app
from tests.corroboration.evaluation.test_cases import make_case

COMMANDS = (
    "build-cases",
    "build-scratch",
    "build-candidates",
    "export-judge-inputs",
    "estimate",
    "import-judge",
    "review",
    "agreement",
    "evaluate",
)


def test_help_lists_every_evaluation_command():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in COMMANDS:
        assert command in result.stdout, command


def _export(tmp_path, count=3):
    cases = [make_case(i, "supports", split="test") for i in range(1, count + 1)]
    source = tmp_path / "v1.jsonl"
    write_cases(source, cases)
    out = tmp_path / "export"
    result = CliRunner().invoke(
        app, ["export-judge-inputs", "--cases", str(source), "--out", str(out), "--seed", "3"]
    )
    assert result.exit_code == 0, result.output
    return out, cases


def test_estimate_prints_calls_and_tokens(tmp_path):
    out, _ = _export(tmp_path, 3)
    result = CliRunner().invoke(app, ["estimate", "--export", str(out)])
    assert result.exit_code == 0, result.output
    assert "calls: 3" in result.stdout
    assert "estimated tokens:" in result.stdout


def test_export_needs_exactly_one_source(tmp_path):
    result = CliRunner().invoke(app, ["export-judge-inputs", "--out", str(tmp_path / "x")])
    assert result.exit_code == 1


def test_import_judge_writes_the_set_and_prints_the_labels(tmp_path):
    out, cases = _export(tmp_path, 3)
    mapping = json.loads((out / "mapping.json").read_text())
    (out / "verdicts").mkdir()
    for input_id in mapping["inputs"]:
        (out / "verdicts" / f"{input_id}.json").write_text('{"label": "related"}')
    set_dir = tmp_path / "v2"
    result = CliRunner().invoke(
        app,
        ["import-judge", "--export", str(out), "--set", str(set_dir), "--judge-model", "judge-x"],
    )
    assert result.exit_code == 0, result.output
    assert "v1 cases pre-labelled: 3" in result.stdout
    assert "problem:" in result.stdout
    assert {c.expected for c in load_cases(set_dir / "cases.jsonl")} == {"related"}
