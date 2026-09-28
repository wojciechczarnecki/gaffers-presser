import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, select
from typer.testing import CliRunner

from app.core.settings import ExtractionSettings
from app.extraction.cli import ExtractionCliDeps, app, build_spec_from_settings
from app.extraction.evaluation.cases import EvalCase, ExpectedEvent, load_cases, write_cases
from app.extraction.evaluation.runner import run_evaluation
from app.extraction.linking import PlayerIndex, PlayerRecord, load_snapshot
from app.extraction.models import Extraction
from app.extraction.pricing import Price
from app.extraction.providers import ChatModelSpec
from app.extraction.schemas import ExtractedEvent, ExtractionOutput
from app.extraction.store import ExtractionRecord, save_extraction
from app.fpl.models.reference import Player, Season, Team
from app.tweets.models import Tweet
from tests.conftest import BACKEND_DIR
from tests.extraction.fakes import FakeChatModel, RecordingHandler

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
EXTRACTION_VARIABLE = re.compile(r"(LLM_.*|LANGFUSE_.*|.*_API_KEY|USD_PLN_RATE)")


@pytest.fixture(autouse=True)
def _no_extraction_variables(monkeypatch):
    # Keys the owner keeps in the environment must never reach a provider or Langfuse here.
    for name in list(os.environ):
        if EXTRACTION_VARIABLE.fullmatch(name):
            monkeypatch.delenv(name)


class FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        pass


def _tweet(x_id: int, created_at: datetime = NOW) -> Tweet:
    return Tweet(
        x_id=x_id,
        author_handle="reporter",
        text=f"Post {x_id}",
        created_at=created_at,
        first_fetched_at=created_at,
        source="list",
        is_repost=False,
        is_reply=False,
        raw={},
    )


def _build_spec(*responses, provider: str = "fake", model: str = "fake-model"):
    fake = FakeChatModel(responses=list(responses))
    spec = ChatModelSpec(provider=provider, model=model, chat_model=fake)

    def build_spec(cli_provider, cli_model):
        return ChatModelSpec(
            provider=cli_provider or provider,
            model=cli_model or model,
            chat_model=fake,
        )

    return build_spec, spec


def _deps(db, build_spec, settings=None, clock=None) -> ExtractionCliDeps:
    return ExtractionCliDeps(
        engine=db,
        settings=settings or ExtractionSettings(_env_file=None),
        build_spec=build_spec,
        clock=clock or FixedClock(NOW),
    )


def test_reextract_by_x_id(db):
    with Session(db) as session:
        session.add(_tweet(1))
        session.commit()
        save_extraction(
            session,
            ExtractionRecord(
                tweet_x_id=1,
                status="extracted",
                provider="old",
                model="old-model",
                prompt_version="v0",
                started_at=NOW,
                finished_at=NOW,
                attempts=1,
            ),
            [],
        )

    build_spec, _ = _build_spec(ExtractionOutput(events=[]))
    result = CliRunner().invoke(app, ["reextract", "--x-id", "1"], obj=_deps(db, build_spec))

    assert result.exit_code == 0
    assert "posts processed: 1" in result.stdout
    assert "events: 0" in result.stdout
    assert "failures: 0" in result.stdout
    assert "total cost: n/a" in result.stdout

    with Session(db) as session:
        rows = session.exec(select(Extraction).where(Extraction.tweet_x_id == 1)).all()
    assert len(rows) == 2
    assert {r.provider for r in rows} == {"old", "fake"}


def test_reextract_range(db):
    with Session(db) as session:
        session.add(_tweet(1, created_at=NOW))
        session.add(_tweet(2, created_at=NOW + timedelta(hours=1)))
        session.add(_tweet(3, created_at=NOW + timedelta(hours=2)))
        session.commit()

    build_spec, _ = _build_spec(ExtractionOutput(events=[]), ExtractionOutput(events=[]))
    result = CliRunner().invoke(
        app,
        [
            "reextract",
            "--since",
            NOW.isoformat(),
            "--until",
            (NOW + timedelta(hours=1)).isoformat(),
        ],
        obj=_deps(db, build_spec),
    )

    assert result.exit_code == 0
    assert "posts processed: 2" in result.stdout
    with Session(db) as session:
        rows = session.exec(select(Extraction)).all()
    assert {r.tweet_x_id for r in rows} == {1, 2}


def test_reextract_failed_only(db):
    with Session(db) as session:
        session.add(_tweet(1))
        session.add(_tweet(2))
        session.commit()
        save_extraction(
            session,
            ExtractionRecord(
                tweet_x_id=1,
                status="failed",
                provider="old",
                model="old-model",
                prompt_version="v0",
                started_at=NOW,
                finished_at=NOW,
                attempts=3,
                error_class="RuntimeError",
            ),
            [],
        )
        save_extraction(
            session,
            ExtractionRecord(
                tweet_x_id=2,
                status="extracted",
                provider="old",
                model="old-model",
                prompt_version="v0",
                started_at=NOW,
                finished_at=NOW,
                attempts=1,
            ),
            [],
        )

    build_spec, _ = _build_spec(ExtractionOutput(events=[]))
    result = CliRunner().invoke(app, ["reextract", "--failed"], obj=_deps(db, build_spec))

    assert result.exit_code == 0
    assert "posts processed: 1" in result.stdout
    with Session(db) as session:
        rows = session.exec(
            select(Extraction).where(Extraction.tweet_x_id == 1, Extraction.status == "extracted")
        ).all()
    assert len(rows) == 1


def test_reextract_with_other_model(db):
    with Session(db) as session:
        session.add(_tweet(1))
        session.commit()

    build_spec, _ = _build_spec(ExtractionOutput(events=[]), provider="fake", model="fake-model")
    result = CliRunner().invoke(
        app,
        ["reextract", "--x-id", "1", "--provider", "other", "--model", "other-model"],
        obj=_deps(db, build_spec),
    )

    assert result.exit_code == 0
    with Session(db) as session:
        row = session.exec(select(Extraction).where(Extraction.tweet_x_id == 1)).one()
    assert row.provider == "other"
    assert row.model == "other-model"


def test_reextract_events_and_failures_counted(db):
    with Session(db) as session:
        session.add(_tweet(1, created_at=NOW))
        session.add(_tweet(2, created_at=NOW + timedelta(hours=1)))
        session.commit()

    build_spec, _ = _build_spec(
        ExtractionOutput(
            events=[
                ExtractedEvent(player="Haaland", team=None, event_type="out", certainty="confirmed")
            ]
        ),
        RuntimeError("boom"),
        RuntimeError("boom"),
        RuntimeError("boom"),
    )
    result = CliRunner().invoke(
        app,
        [
            "reextract",
            "--since",
            NOW.isoformat(),
            "--until",
            (NOW + timedelta(hours=1)).isoformat(),
        ],
        obj=_deps(db, build_spec),
    )

    assert result.exit_code == 0
    assert "posts processed: 2" in result.stdout
    assert "events: 1" in result.stdout
    assert "failures: 1" in result.stdout


def test_reextract_requires_one_selector(db):
    build_spec, _ = _build_spec()
    result = CliRunner().invoke(app, ["reextract"], obj=_deps(db, build_spec))
    assert result.exit_code == 1
    assert "exactly one" in result.stderr

    result = CliRunner().invoke(
        app, ["reextract", "--x-id", "1", "--failed"], obj=_deps(db, build_spec)
    )
    assert result.exit_code == 1
    assert "exactly one" in result.stderr

    result = CliRunner().invoke(
        app, ["reextract", "--since", NOW.isoformat()], obj=_deps(db, build_spec)
    )
    assert result.exit_code == 1


def test_reextract_config_error_names_variable(db, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = ExtractionSettings(_env_file=None, llm_provider="", llm_model="")
    build_spec = build_spec_from_settings(settings)
    result = CliRunner().invoke(
        app,
        ["reextract", "--x-id", "1", "--provider", "openai"],
        obj=_deps(db, build_spec, settings=settings),
    )
    assert result.exit_code == 1
    assert "OPENAI_API_KEY" in result.stderr


def test_help():
    result = subprocess.run(
        [sys.executable, "-m", "app.extraction", "--help"],
        capture_output=True,
        text=True,
        cwd=str(BACKEND_DIR),
    )
    assert result.returncode == 0
    assert "reextract" in result.stdout

    result = subprocess.run(
        [sys.executable, "-m", "app.extraction", "reextract", "--help"],
        capture_output=True,
        text=True,
        cwd=str(BACKEND_DIR),
    )
    assert result.returncode == 0


def _seed_players(session, season: str, fpl_id: int, web_name: str) -> None:
    session.add(Season(label=season))
    session.add(Team(season=season, fpl_id=1, name="Team One", short_name="ONE"))
    session.flush()
    session.add(
        Player(
            season=season,
            fpl_id=fpl_id,
            web_name=web_name,
            first_name="First",
            second_name="Last",
            team_fpl_id=1,
            position=1,
        )
    )
    session.commit()


def test_snapshot_players_writes_the_latest_season(db, tmp_path):
    with Session(db) as session:
        _seed_players(session, "2025/26", 1, "Old")
        _seed_players(session, "2026/27", 2, "Current")
    build_spec, _ = _build_spec()
    output = tmp_path / "players.json"

    result = CliRunner().invoke(
        app, ["snapshot-players", "--output", str(output)], obj=_deps(db, build_spec)
    )

    assert result.exit_code == 0, result.output
    assert output.exists()
    players, teams = load_snapshot(output)
    assert [p.web_name for p in players] == ["Current"]
    assert players[0].season == "2026/27"
    assert [t.season for t in teams] == ["2026/27"]


def _seed_haaland(session) -> None:
    session.add(Season(label="2026/27"))
    session.add(Team(season="2026/27", fpl_id=1, name="Man City", short_name="MCI"))
    session.flush()
    session.add(
        Player(
            season="2026/27",
            fpl_id=5,
            web_name="Haaland",
            first_name="Erling",
            second_name="Haaland",
            team_fpl_id=1,
            position=4,
        )
    )
    session.commit()


def _haaland_out(certainty="confirmed") -> ExtractionOutput:
    return ExtractionOutput(
        events=[ExtractedEvent(player="Haaland", team=None, event_type="out", certainty=certainty)]
    )


def _existing_case(case_id: str) -> EvalCase:
    return EvalCase(
        id=case_id,
        author_handle="reporter",
        text="existing",
        created_at=NOW,
        is_repost=False,
        is_reply=False,
        split="test",
        synthetic=False,
        reviewed=True,
        tags=[],
        expected_events=[
            ExpectedEvent(mention="X", fpl_id=None, event_type="out", certainty="confirmed")
        ],
    )


def test_prelabel_writes_unreviewed_candidates_with_the_split_rule(db, tmp_path):
    with Session(db) as session:
        _seed_haaland(session)
        for x_id in (10, 11, 13):  # 10 % 10 = 0 and 13 % 10 = 3
            session.add(_tweet(x_id, created_at=NOW + timedelta(minutes=x_id)))
        session.commit()
    build_spec, _ = _build_spec(_haaland_out(), ExtractionOutput(events=[]), _haaland_out())
    output = tmp_path / "cases.jsonl"

    result = CliRunner().invoke(
        app,
        ["prelabel", "--output", str(output), "--provider", "fake", "--model", "m"],
        obj=_deps(db, build_spec),
    )

    assert result.exit_code == 0, result.output
    cases = {c.id: c for c in load_cases(output)}
    assert set(cases) == {"10", "11", "13"}
    assert all(
        c.reviewed is False and c.synthetic is False and c.tags == [] for c in cases.values()
    )
    assert cases["10"].split == "dev"
    assert cases["11"].split == "dev"
    assert cases["13"].split == "test"
    assert cases["10"].author_handle == "reporter"
    assert cases["10"].text == "Post 10"
    assert [
        (e.mention, e.fpl_id, e.event_type, e.certainty) for e in cases["10"].expected_events
    ] == [("Haaland", 5, "out", "confirmed")]
    assert cases["11"].expected_events == []
    assert "cases written: 3" in result.stdout


def test_prelabel_skips_ids_already_in_the_set(db, tmp_path):
    with Session(db) as session:
        _seed_haaland(session)
        for x_id in (1, 2, 3):
            session.add(_tweet(x_id, created_at=NOW + timedelta(minutes=x_id)))
        session.commit()
    output = tmp_path / "cases.jsonl"
    write_cases(output, [_existing_case("1")])
    eval_set = tmp_path / "other.jsonl"
    write_cases(eval_set, [_existing_case("2")])
    build_spec, _ = _build_spec(ExtractionOutput(events=[]))

    result = CliRunner().invoke(
        app,
        ["prelabel", "--output", str(output), "--eval-set", str(eval_set), "--provider", "f"],
        obj=_deps(db, build_spec),
    )

    assert result.exit_code == 0, result.output
    cases = load_cases(output)
    assert [c.id for c in cases] == ["1", "3"]
    assert cases[0].reviewed is True  # the existing case is untouched
    assert "skipped: 2" in result.stdout


def test_prelabel_limit_and_range(db, tmp_path):
    with Session(db) as session:
        _seed_haaland(session)
        for x_id in (1, 2, 3, 4):
            session.add(_tweet(x_id, created_at=NOW + timedelta(hours=x_id)))
        session.commit()
    build_spec, _ = _build_spec(ExtractionOutput(events=[]), ExtractionOutput(events=[]))
    output = tmp_path / "cases.jsonl"

    result = CliRunner().invoke(
        app,
        [
            "prelabel",
            "--output",
            str(output),
            "--since",
            (NOW + timedelta(hours=2)).isoformat(),
            "--limit",
            "2",
        ],
        obj=_deps(db, build_spec),
    )

    assert result.exit_code == 0, result.output
    assert [c.id for c in load_cases(output)] == ["2", "3"]


def test_prelabel_config_error_names_variable(db, tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = ExtractionSettings(_env_file=None, llm_provider="", llm_model="")
    output = tmp_path / "cases.jsonl"
    result = CliRunner().invoke(
        app,
        ["prelabel", "--output", str(output), "--provider", "openai", "--model", "m"],
        obj=_deps(db, build_spec_from_settings(settings), settings=settings),
    )
    assert result.exit_code == 1
    assert "OPENAI_API_KEY" in result.stderr
    assert not output.exists()


def _snapshot(path):
    path.write_text(
        json.dumps(
            {
                "season": "2026/27",
                "players": [
                    {
                        "season": "2026/27",
                        "fpl_id": 5,
                        "web_name": "Haaland",
                        "first_name": "Erling",
                        "second_name": "Haaland",
                        "team_fpl_id": 1,
                    }
                ],
                "teams": [
                    {"season": "2026/27", "fpl_id": 1, "name": "Man City", "short_name": "MCI"}
                ],
            }
        )
    )


def _eval_case(case_id: str, split: str, reviewed: bool = True, events: bool = True) -> EvalCase:
    return EvalCase(
        id=case_id,
        author_handle="reporter",
        text=f"Haaland is out {case_id}",
        created_at=NOW,
        is_repost=False,
        is_reply=False,
        split=split,
        synthetic=False,
        reviewed=reviewed,
        tags=[],
        expected_events=(
            [ExpectedEvent(mention="Haaland", fpl_id=5, event_type="out", certainty="confirmed")]
            if events
            else []
        ),
    )


class _Files:
    def __init__(self, tmp_path, cases):
        self.cases = tmp_path / "cases.jsonl"
        self.players = tmp_path / "players.json"
        self.results = tmp_path / "results"
        write_cases(self.cases, cases)
        _snapshot(self.players)

    def args(self, *extra, split="test"):
        return [
            "evaluate",
            "--split",
            split,
            "--provider",
            "fake",
            "--model",
            "m",
            "--cases",
            str(self.cases),
            "--players",
            str(self.players),
            "--output-dir",
            str(self.results),
            *extra,
        ]


def _no_db_deps(build_spec, settings=None) -> ExtractionCliDeps:
    return ExtractionCliDeps(
        engine=None,
        settings=settings or ExtractionSettings(_env_file=None, usd_pln_rate=4.0),
        build_spec=build_spec,
        clock=FixedClock(NOW),
    )


def _forbidden_build_spec(provider, model):
    raise AssertionError("no model may be built")


def test_evaluate_refuses_unreviewed(tmp_path):
    files = _Files(
        tmp_path,
        [_eval_case("1", "test", reviewed=False), _eval_case("2", "test", reviewed=False)],
    )
    # Neither USD_PLN_RATE nor a key is set: the reviewed check comes first.
    deps = _no_db_deps(_forbidden_build_spec, ExtractionSettings(_env_file=None, usd_pln_rate=None))

    result = CliRunner().invoke(app, files.args(), obj=deps)

    assert result.exit_code == 1
    assert "2 cases of the test split are not reviewed" in result.stderr
    assert not files.results.exists()


def test_evaluate_requires_pln_rate(tmp_path):
    files = _Files(tmp_path, [_eval_case("1", "test")])
    deps = _no_db_deps(_forbidden_build_spec, ExtractionSettings(_env_file=None, usd_pln_rate=None))

    result = CliRunner().invoke(app, files.args(), obj=deps)

    assert result.exit_code == 1
    assert "USD_PLN_RATE" in result.stderr


def test_evaluate_config_error_names_variable(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    files = _Files(tmp_path, [_eval_case("1", "test")])
    settings = ExtractionSettings(_env_file=None, llm_provider="", llm_model="", usd_pln_rate=4.0)
    deps = _no_db_deps(build_spec_from_settings(settings), settings)

    args = files.args()
    args[args.index("fake")] = "openai"
    result = CliRunner().invoke(app, args, obj=deps)

    assert result.exit_code == 1
    assert "OPENAI_API_KEY" in result.stderr


def test_evaluate_writes_results(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.extraction.evaluation.runner.load_prices",
        lambda: {"fake:m": Price(input_per_million=1.0, output_per_million=2.0, checked="x")},
    )
    files = _Files(tmp_path, [_eval_case("1", "test"), _eval_case("2", "test", events=False)])
    build_spec, _ = _build_spec(_haaland_out(), _haaland_out(), model="m")

    result = CliRunner().invoke(
        app,
        files.args("--run-name", "run-1", "--posts-per-month", "1050"),
        obj=_no_db_deps(build_spec),
    )

    assert result.exit_code == 0, result.output
    data = json.loads((files.results / "run-1.json").read_text())
    assert (data["run_name"], data["provider"], data["model"]) == ("run-1", "fake", "m")
    assert data["prompt_version"].startswith("extraction@")
    assert data["split"] == "test"
    metrics = data["metrics"]
    for key in (
        "precision",
        "recall",
        "f1",
        "linking_accuracy",
        "false_alarm_rate",
        "certainty_accuracy",
        "certainty_confusion",
        "latency_p50_seconds",
        "latency_p95_seconds",
        "mean_input_tokens",
        "mean_output_tokens",
        "mean_cost_usd",
        "projected_monthly_cost_pln",
        "passes",
    ):
        assert key in metrics, key
    # case 1 right, case 2 (no expected events) got one event: a false alarm and an extra event
    assert metrics["recall"] == 1.0
    assert metrics["precision"] == 0.5
    assert metrics["false_alarm_rate"] == 1.0
    assert metrics["linking_accuracy"] == 1.0
    assert metrics["mean_input_tokens"] == 10
    assert metrics["mean_output_tokens"] == 5
    assert metrics["mean_cost_usd"] == pytest.approx(2e-5)
    assert metrics["projected_monthly_cost_pln"] == pytest.approx(2e-5 * 1050 * 4.0)
    assert metrics["passes"] is False
    by_id = {c["id"]: c for c in data["case_results"]}
    assert by_id["1"]["predicted"][0]["fpl_id"] == 5
    assert by_id["1"]["expected"][0]["event_type"] == "out"
    assert "f1" in result.stdout


def test_evaluate_only_selected_split(tmp_path):
    files = _Files(
        tmp_path,
        [_eval_case("1", "test"), _eval_case("2", "dev", reviewed=False)],
    )
    build_spec, _ = _build_spec(_haaland_out())  # a second call would raise IndexError

    result = CliRunner().invoke(app, files.args("--run-name", "r"), obj=_no_db_deps(build_spec))

    assert result.exit_code == 0, result.output
    data = json.loads((files.results / "r.json").read_text())
    assert [c["id"] for c in data["case_results"]] == ["1"]


def test_evaluate_case_error_is_recorded_and_run_continues(tmp_path):
    files = _Files(tmp_path, [_eval_case("1", "test"), _eval_case("2", "test")])
    build_spec, _ = _build_spec(
        RuntimeError("boom"), RuntimeError("boom"), RuntimeError("boom"), _haaland_out()
    )

    result = CliRunner().invoke(app, files.args("--run-name", "r"), obj=_no_db_deps(build_spec))

    assert result.exit_code == 0, result.output
    data = json.loads((files.results / "r.json").read_text())
    assert data["errored_cases"] == 1
    by_id = {c["id"]: c for c in data["case_results"]}
    assert by_id["1"]["error_class"] == "RuntimeError"
    assert by_id["1"]["attempts"] == 3
    assert by_id["1"]["predicted"] == []
    assert by_id["2"]["error_class"] is None
    assert data["metrics"]["recall"] == 0.5


def test_evaluate_traces_with_run_name():
    handler = RecordingHandler()
    fake = FakeChatModel(responses=[_haaland_out()])
    spec = ChatModelSpec(provider="fake", model="m", chat_model=fake)
    players = [
        PlayerRecord(
            season="2026/27",
            fpl_id=5,
            web_name="Haaland",
            first_name="Erling",
            second_name="Haaland",
            team_fpl_id=1,
        )
    ]

    run_evaluation(
        [_eval_case("1", "test")],
        spec,
        PlayerIndex(players, []),
        handler,
        run_name="eval-run-7",
        split="test",
        clock=FixedClock(NOW),
        posts_per_month=1050,
        usd_pln_rate=4.0,
    )

    assert handler.chat_model_starts
    for start in handler.chat_model_starts:
        assert start["metadata"]["langfuse_session_id"] == "eval-run-7"
        assert start["metadata"]["x_id"] == "1"


@pytest.mark.parametrize("run_name", ["gemini/flash-v1", "../x", ".hidden", "a b"])
def test_evaluate_rejects_unsafe_run_name_before_any_call(tmp_path, run_name):
    files = _Files(tmp_path, [_eval_case("1", "test")])

    result = CliRunner().invoke(
        app, files.args("--run-name", run_name), obj=_no_db_deps(_forbidden_build_spec)
    )

    assert result.exit_code == 1
    assert "--run-name" in result.stderr
    assert not files.results.exists()


def test_malformed_variable_is_named_without_its_value(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("USD_PLN_RATE", "4,05")

    result = CliRunner().invoke(app, ["evaluate", "--split", "test"])

    assert result.exit_code == 1
    assert "USD_PLN_RATE" in result.stderr
    assert "4,05" not in result.stderr
