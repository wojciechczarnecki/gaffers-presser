import typer.main
from typer.testing import CliRunner

from app.retrieval.cli import app as main_app
from app.retrieval.evaluation.cli import app as evaluation_app


def names(app) -> set[str]:
    return {
        command.name or command.callback.__name__
        for command in typer.main.get_command(app).commands.values()
    }


def test_main_cli_keeps_index_status_search_only():
    assert names(main_app) == {"index", "status", "search"}


def test_evaluation_cli_has_exactly_the_commands():
    assert names(evaluation_app) == {
        "export-corpus",
        "build-queries",
        "build-player-slice",
        "carry-v1",
        "prelabel",
        "export-judge-inputs",
        "import-judge",
        "agreement",
        "estimate",
        "review",
        "evaluate",
        "compare",
    }


def test_evaluation_cli_help_exits_0():
    result = CliRunner().invoke(evaluation_app, ["--help"])
    assert result.exit_code == 0
    assert "export-corpus" in result.stdout


def test_old_evaluation_commands_are_not_aliased():
    for command in ("evaluate", "prelabel", "review", "export-corpus", "build-queries"):
        result = CliRunner().invoke(main_app, [command, "--help"])
        assert result.exit_code == 2, command
        assert "No such command" in result.output
