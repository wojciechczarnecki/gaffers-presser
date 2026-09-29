import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, select
from typer.testing import CliRunner

from app.core.errors import ConfigError
from app.extraction.cli import (
    DEFAULT_RESULTS_DIR,
    ExtractionCliDeps,
    app,
    build_spec_from_settings,
    default_output_dir,
    host_lookup_from_settings,
)
from app.extraction.config import ExtractionSettings
from app.extraction.evaluation.cases import EvalCase, ExpectedEvent, load_cases, write_cases
from app.extraction.evaluation.runner import run_evaluation
from app.extraction.generation import HostLookup
from app.extraction.linking import PlayerIndex, PlayerRecord, load_snapshot
from app.extraction.model_settings import ModelSettings
from app.extraction.models import Extraction
from app.extraction.providers import ChatModelSpec
from app.extraction.schemas import ExtractedEvent, ExtractionOutput
from app.extraction.store import ExtractionRecord, save_extraction
from app.fpl.models.reference import Player, Season, Team
from app.llm.pricing import Price
from app.tweets.models import Tweet
from tests.conftest import BACKEND_DIR
from tests.extraction.fakes import FakeChatModel, RecordingHandler

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
SENTINEL = "sentinel-secret-value"
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


def _build_spec(*responses, provider: str = "fake", model: str = "fake-model", **fake_fields):
    fake = FakeChatModel(responses=list(responses), **fake_fields)
    spec = ChatModelSpec(provider=provider, model=model, chat_model=fake)
    fallback_asked: list[bool] = []
    row = ModelSettings(
        reasoning_effort="none", temperature=True, structured_method="function_calling", checked="x"
    )

    def build_spec(cli_model, *, fallback):
        fallback_asked.append(fallback)
        return ChatModelSpec(
            provider=provider, model=cli_model or model, chat_model=fake, settings=row
        )

    build_spec.fallback_asked = fallback_asked
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
        ["reextract", "--x-id", "1", "--model", "other/model"],
        obj=_deps(db, build_spec),
    )

    assert result.exit_code == 0
    with Session(db) as session:
        row = session.exec(select(Extraction).where(Extraction.tweet_x_id == 1)).one()
    assert row.provider == "fake"
    assert row.model == "other/model"
    assert build_spec.fallback_asked == [True]


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


def test_reextract_without_key_names_openrouter_variable(db, monkeypatch):
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", SENTINEL)
    settings = ExtractionSettings(_env_file=None)
    build_spec = build_spec_from_settings(settings)
    result = CliRunner().invoke(
        app,
        ["reextract", "--x-id", "1"],
        obj=_deps(db, build_spec, settings=settings),
    )
    assert result.exit_code == 1
    assert "OPENROUTER_API_KEY" in result.stderr
    assert SENTINEL not in result.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["reextract", "--x-id", "1"],
        ["prelabel", "--output", "out.jsonl"],
        ["evaluate", "--split", "dev"],
    ],
)
def test_provider_option_removed(args):
    build_spec, _ = _build_spec()
    result = CliRunner().invoke(app, [*args, "--provider", "openai"], obj=_no_db_deps(build_spec))
    assert result.exit_code == 2


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
        ["prelabel", "--output", str(output), "--model", "m"],
        obj=_deps(db, build_spec),
    )

    assert result.exit_code == 0, result.output
    assert build_spec.fallback_asked == [False]
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
        ["prelabel", "--output", str(output), "--eval-set", str(eval_set)],
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


def test_prelabel_without_key_names_openrouter_variable(db, tmp_path, monkeypatch):
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", SENTINEL)
    settings = ExtractionSettings(_env_file=None)
    output = tmp_path / "cases.jsonl"
    result = CliRunner().invoke(
        app,
        ["prelabel", "--output", str(output), "--model", "google/gemini-3.1-flash-lite"],
        obj=_deps(db, build_spec_from_settings(settings), settings=settings),
    )
    assert result.exit_code == 1
    assert "OPENROUTER_API_KEY" in result.stderr
    assert SENTINEL not in result.stderr
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


def _forbidden_build_spec(model, *, fallback):
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


def test_evaluate_without_key_names_openrouter_variable(tmp_path, monkeypatch):
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", SENTINEL)
    files = _Files(tmp_path, [_eval_case("1", "test")])
    settings = ExtractionSettings(_env_file=None, usd_pln_rate=4.0)
    deps = _no_db_deps(build_spec_from_settings(settings), settings)

    result = CliRunner().invoke(app, files.args(), obj=deps)

    assert result.exit_code == 1
    assert "OPENROUTER_API_KEY" in result.stderr
    assert SENTINEL not in result.stderr


def test_evaluate_shows_reasoning_tokens(tmp_path):
    files = _Files(tmp_path, [_eval_case("1", "test")])
    build_spec, _ = _build_spec(_haaland_out(), model="m", reasoning_tokens=3, host="Fireworks")

    result = CliRunner().invoke(app, files.args("--run-name", "run-r"), obj=_no_db_deps(build_spec))

    assert result.exit_code == 0, result.output
    assert "mean reasoning tokens: 3.00" in result.stdout
    assert "serving hosts: Fireworks 1" in result.stdout
    assert "f1 >= 0.85: yes" in result.stdout
    assert "no errored case: yes" in result.stdout


def test_evaluate_fills_the_host_from_the_generation_lookup(tmp_path):
    files = _Files(tmp_path, [_eval_case("1", "test"), _eval_case("2", "test")])
    build_spec, _ = _build_spec(_haaland_out(), _haaland_out(), model="m", generation_id="gen-1")
    looked_up: list[str] = []

    def lookup(generation_id):
        looked_up.append(generation_id)
        return "DeepInfra"

    deps = _no_db_deps(build_spec)
    deps = ExtractionCliDeps(
        engine=None,
        settings=deps.settings,
        build_spec=build_spec,
        clock=deps.clock,
        host_lookup=lambda: lookup,
    )

    result = CliRunner().invoke(app, files.args("--run-name", "run-h"), obj=deps)

    assert result.exit_code == 0, result.output
    assert looked_up == ["gen-1", "gen-1"]
    assert "serving hosts: DeepInfra 2" in result.stdout
    data = json.loads((files.results / "run-h.json").read_text())
    assert data["metrics"]["hosts"] == {"DeepInfra": 2}
    assert data["case_results"][0]["generation_id"] == "gen-1"


def test_evaluate_closes_the_host_lookup(tmp_path):
    files = _Files(tmp_path, [_eval_case("1", "test")])
    build_spec, _ = _build_spec(_haaland_out(), model="m", generation_id="gen-1")

    class _Lookup:
        closed = False

        def __call__(self, generation_id):
            return "DeepInfra"

        def close(self):
            self.closed = True

    lookup = _Lookup()
    base = _no_db_deps(build_spec)
    deps = ExtractionCliDeps(
        engine=None,
        settings=base.settings,
        build_spec=build_spec,
        clock=base.clock,
        host_lookup=lambda: lookup,
    )

    result = CliRunner().invoke(app, files.args("--run-name", "run-c"), obj=deps)

    assert result.exit_code == 0, result.output
    assert lookup.closed


def test_evaluate_writes_results(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.extraction.evaluation.runner.load_prices",
        lambda: {"m": Price(input_per_million=1.0, output_per_million=2.0, checked="x")},
    )
    files = _Files(tmp_path, [_eval_case("1", "test"), _eval_case("2", "test", events=False)])
    build_spec, _ = _build_spec(
        _haaland_out(),
        _haaland_out(),
        model="m",
        response_model="m",
        host="Fireworks",
        reasoning_tokens=3,
        reported_cost=0.00005,
    )

    result = CliRunner().invoke(
        app,
        files.args("--run-name", "run-1", "--posts-per-month", "1050"),
        obj=_no_db_deps(build_spec),
    )

    assert result.exit_code == 0, result.output
    data = json.loads((files.results / "run-1.json").read_text())
    assert build_spec.fallback_asked == [False]
    assert data["reasoning_effort"] == "none"
    assert data["temperature"] is True
    assert data["structured_method"] == "function_calling"
    assert data["metrics"]["hosts"] == {"Fireworks": 2}
    assert data["metrics"]["thresholds_passed"]["f1"] is False  # 2 * 1 / (2 + 1) = 0.667
    assert data["metrics"]["thresholds_passed"]["linking_accuracy"] is True
    assert data["metrics"]["total_cost_usd"] == pytest.approx(4e-5)
    by_case = {c["id"]: c for c in data["case_results"]}
    assert by_case["1"]["host"] == "Fireworks"
    assert by_case["1"]["answered_model"] == "m"
    assert by_case["1"]["reasoning_tokens"] == 3
    assert by_case["1"]["reported_cost_usd"] == 0.00005
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


def _compare_case(case_id, split, *events):
    return EvalCase(
        id=case_id,
        author_handle="reporter",
        text=f"post {case_id}",
        created_at=NOW,
        is_repost=False,
        is_reply=False,
        split=split,
        synthetic=False,
        reviewed=True,
        tags=[],
        expected_events=[
            ExpectedEvent(mention=m, fpl_id=i, event_type="out", certainty=c) for m, i, c in events
        ],
    )


def _reviewed_and_baseline():
    reviewed = [
        _compare_case("a", "dev", ("Salah", 1, "likely")),
        _compare_case("b", "test", ("Haaland", 2, "confirmed"), ("Saka", 3, "confirmed")),
        _compare_case("c", "test"),
    ]
    baseline = [
        _compare_case("a", "dev", ("Salah", 1, "confirmed")),
        _compare_case("b", "test", ("Haaland", 2, "confirmed")),
        _compare_case("c", "test", ("Kane", 4, "confirmed")),
    ]
    return reviewed, baseline


def test_compare_labels_with_baseline_file(tmp_path):
    reviewed, baseline = _reviewed_and_baseline()
    cases, base = tmp_path / "cases.jsonl", tmp_path / "base.jsonl"
    write_cases(cases, reviewed)
    write_cases(base, baseline)

    result = CliRunner().invoke(
        app, ["compare-labels", "--cases", str(cases), "--baseline", str(base)]
    )

    assert result.exit_code == 0, result.output
    out = result.stdout
    dev, test = out.split("split test")
    assert "split dev" in dev
    assert "cases: 1" in dev and "cases changed: 1" in dev
    assert "events added / removed / relabelled: 0 / 0 / 1" in dev
    assert "relabelled by field: event_type 0, certainty 1, fpl_id 0" in dev
    assert "pre-labels precision / recall / f1: 1.000 / 1.000 / 1.000" in dev
    assert "cases: 2" in test and "cases changed: 2" in test
    assert "events added / removed / relabelled: 1 / 1 / 0" in test
    assert "pre-labels precision / recall / f1: 0.500 / 0.500 / 0.500" in test


def test_compare_labels_at_git_revision(tmp_path):
    reviewed, baseline = _reviewed_and_baseline()
    cases = tmp_path / "cases.jsonl"

    def git(*args):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    write_cases(cases, baseline)
    git("add", "cases.jsonl")
    git("commit", "-q", "-m", "prelabels")
    write_cases(cases, reviewed)
    git("add", "cases.jsonl")
    git("commit", "-q", "-m", "reviewed")

    result = CliRunner().invoke(
        app, ["compare-labels", "--cases", str(cases), "--revision", "HEAD~1"]
    )

    assert result.exit_code == 0, result.output
    assert "events added / removed / relabelled: 1 / 1 / 0" in result.stdout
    assert "events added / removed / relabelled: 0 / 0 / 1" in result.stdout


def test_compare_labels_unknown_revision_fails(tmp_path):
    reviewed, _ = _reviewed_and_baseline()
    cases = tmp_path / "cases.jsonl"
    write_cases(cases, reviewed)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)

    result = CliRunner().invoke(
        app, ["compare-labels", "--cases", str(cases), "--revision", "nope"]
    )

    assert result.exit_code == 1
    assert "cannot read cases.jsonl at revision nope" in result.stderr


def test_dev_runs_default_to_the_ignored_directory():
    assert default_output_dir("dev") == DEFAULT_RESULTS_DIR / "dev"
    assert default_output_dir("test") == DEFAULT_RESULTS_DIR

    def ignored(path: str) -> int:
        return subprocess.run(
            ["git", "check-ignore", "-q", path],
            cwd=str(BACKEND_DIR.parent),
            capture_output=True,
        ).returncode

    assert ignored("backend/evals/extraction/results/dev/x.json") == 0
    assert ignored("backend/evals/extraction/results/x.json") == 1


def test_evaluate_dev_writes_into_the_dev_directory(tmp_path, monkeypatch):
    monkeypatch.setattr("app.extraction.cli.DEFAULT_RESULTS_DIR", tmp_path / "results")
    files = _Files(tmp_path, [_eval_case("1", "dev")])
    build_spec, _ = _build_spec(_haaland_out(), model="m")
    args = files.args("--run-name", "d1", split="dev")
    del args[args.index("--output-dir") : args.index("--output-dir") + 2]

    result = CliRunner().invoke(app, args, obj=_no_db_deps(build_spec))

    assert result.exit_code == 0, result.output
    assert (tmp_path / "results" / "dev" / "d1.json").exists()


def _run_file(directory, name, cost, reported=None, with_totals=True, split="test"):
    directory.mkdir(parents=True, exist_ok=True)
    metrics = {"cases": 2}
    if with_totals:
        metrics["total_cost_usd"] = cost
        metrics["total_reported_cost_usd"] = reported or 0.0
    (directory / f"{name}.json").write_text(
        json.dumps(
            {
                "run_name": name,
                "split": split,
                "model": "a/m",
                "cases": 2,
                "metrics": metrics,
                "case_results": [
                    {"id": "1", "cost_usd": cost / 2, "reported_cost_usd": (reported or 0) / 2},
                    {"id": "2", "cost_usd": cost / 2, "reported_cost_usd": (reported or 0) / 2},
                ],
            }
        )
    )


def test_spend_sums_run_files_recursively(tmp_path):
    _run_file(tmp_path / "results", "test-run", 0.25, reported=0.3)
    _run_file(tmp_path / "results" / "dev", "dev-run", 0.5, reported=0.6, with_totals=False)

    result = CliRunner().invoke(
        app, ["spend", "--results-dir", str(tmp_path / "results")], obj=_no_db_deps(None)
    )

    assert result.exit_code == 0, result.output
    assert "test-run" in result.stdout
    assert "dev-run" in result.stdout
    assert "total cost: 0.7500 USD (prices.toml)" in result.stdout
    assert "total reported cost: 0.9000 USD (OpenRouter)" in result.stdout


def test_spend_empty_directory(tmp_path):
    result = CliRunner().invoke(
        app, ["spend", "--results-dir", str(tmp_path / "none")], obj=_no_db_deps(None)
    )

    assert result.exit_code == 0, result.output
    assert "total cost: 0.0000 USD (prices.toml)" in result.stdout
    assert "total reported cost: 0.0000 USD (OpenRouter)" in result.stdout


LUNA, GEMINI = "openai/gpt-6-luna", "google/gemini-3.1-flash-lite"


def _keyed_settings() -> ExtractionSettings:
    return ExtractionSettings(
        _env_file=None, openrouter_api_key="sk-test", llm_model=LUNA, llm_fallback_model=GEMINI
    )


def test_build_spec_from_settings_sends_the_fallback_only_when_asked():
    build_spec = build_spec_from_settings(_keyed_settings())

    single = build_spec(None, fallback=False)
    assert single.model == LUNA
    assert "models" not in single.chat_model._default_params

    pair = build_spec(None, fallback=True)
    assert pair.chat_model._default_params["models"] == [LUNA, GEMINI]

    override = build_spec(GEMINI, fallback=False)
    assert override.model == GEMINI
    assert "models" not in override.chat_model._default_params


def test_build_spec_from_settings_without_key_raises():
    build_spec = build_spec_from_settings(ExtractionSettings(_env_file=None))
    with pytest.raises(ConfigError, match="OPENROUTER_API_KEY"):
        build_spec(None, fallback=False)


def test_host_lookup_from_settings_needs_the_key():
    assert host_lookup_from_settings(ExtractionSettings(_env_file=None)) is None
    lookup = host_lookup_from_settings(_keyed_settings())
    assert isinstance(lookup, HostLookup)
    lookup.close()
