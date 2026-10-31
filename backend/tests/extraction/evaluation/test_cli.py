import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session
from typer.testing import CliRunner

from app.extraction.cli import ExtractionCliDeps, build_spec_from_settings
from app.extraction.config import ExtractionSettings
from app.extraction.evaluation.cases import EvalCase, ExpectedEvent, load_cases, write_cases
from app.extraction.evaluation.cli import (
    DEFAULT_RESULTS_DIR,
    V1_BASELINE_REVISION,
    app,
    default_output_dir,
)
from app.extraction.evaluation.runner import run_evaluation
from app.extraction.fixtures import load_fixtures_snapshot
from app.extraction.linking import PlayerIndex, PlayerRecord, load_snapshot
from app.extraction.schemas import ExtractedEvent, ExtractionOutput, MatchCheck
from app.fpl.models.reference import Fixture, Player, Season, Team
from app.llm.chat import ChatModelSpec
from app.llm.models import ModelSettings
from app.llm.pricing import Price
from app.tweets.models import Tweet
from tests.conftest import BACKEND_DIR
from tests.extraction.fakes import FakeChatModel, RecordingExtractionTracer

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
    output = tmp_path / "set" / "players.json"

    result = CliRunner().invoke(
        app, ["snapshot-players", "--set", str(output.parent)], obj=_deps(db, build_spec)
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
            "--set",
            str(self.cases.parent),
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


def test_evaluate_writes_header_and_guard_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.extraction.evaluation.runner.load_prices",
        lambda: {"m": Price(input_per_million=1.0, output_per_million=2.0, checked="x")},
    )
    set_dir = tmp_path / "v1"
    set_dir.mkdir()
    files = _Files(set_dir, [_eval_case("1", "test")])
    build_spec, _ = _build_spec(_haaland_out(), model="m", response_model="m")

    first = CliRunner().invoke(app, files.args("--run-name", "run-h"), obj=_no_db_deps(build_spec))
    assert first.exit_code == 0, first.output
    path = files.results / "run-h.json"
    header = json.loads(path.read_text())["header"]
    assert header["set_version"] == "v1"
    assert header["model"] == "m"
    assert header["prompt_version"].startswith("extraction@")
    assert header["reasoning_effort"] == "none"
    assert header["date"] == NOW.isoformat()

    again = CliRunner().invoke(app, files.args("--run-name", "run-h"), obj=_no_db_deps(build_spec))
    assert again.exit_code == 1
    assert str(path) in again.output
    forced = CliRunner().invoke(
        app, files.args("--run-name", "run-h", "--force"), obj=_no_db_deps(build_spec)
    )
    assert forced.exit_code == 0, forced.output


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
    tracer = RecordingExtractionTracer()
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
        tracer,
        run_name="eval-run-7",
        split="test",
        clock=FixedClock(NOW),
        posts_per_month=1050,
        usd_pln_rate=4.0,
    )

    (span,) = tracer.spans
    assert span["session_id"] == "eval-run-7"
    assert span["input"]["x_id"] == "1"
    assert span["outputs"] == [{"events": 1, "attempts": 1}]
    assert len(span["generations"]) == 1


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
        app, ["compare-labels", "--set", str(cases.parent), "--baseline", str(base)]
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
        app, ["compare-labels", "--set", str(cases.parent), "--revision", "HEAD~1"]
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
        app, ["compare-labels", "--set", str(cases.parent), "--revision", "nope"]
    )

    assert result.exit_code == 1
    assert "cannot read cases.jsonl at revision nope" in result.stderr


def test_dev_runs_default_to_the_ignored_directory():
    assert default_output_dir("dev", "v1") == DEFAULT_RESULTS_DIR / "dev"
    assert default_output_dir("test", "v1") == DEFAULT_RESULTS_DIR
    assert default_output_dir("dev", "v2") == DEFAULT_RESULTS_DIR / "v2" / "dev"
    assert default_output_dir("test", "v2") == DEFAULT_RESULTS_DIR / "v2"

    def ignored(path: str) -> int:
        return subprocess.run(
            ["git", "check-ignore", "-q", path],
            cwd=str(BACKEND_DIR.parent),
            capture_output=True,
        ).returncode

    assert ignored("backend/evals/extraction/results/dev/x.json") == 0
    assert ignored("backend/evals/extraction/results/x.json") == 1
    assert ignored("backend/evals/extraction/results/v2/dev/x.json") == 0
    assert ignored("backend/evals/extraction/results/v2/x.json") == 1


def test_evaluate_dev_writes_into_the_dev_directory(tmp_path, monkeypatch):
    monkeypatch.setattr("app.extraction.evaluation.cli.DEFAULT_RESULTS_DIR", tmp_path / "results")
    set_dir = tmp_path / "v1"
    set_dir.mkdir()
    files = _Files(set_dir, [_eval_case("1", "dev")])
    build_spec, _ = _build_spec(_haaland_out(), model="m")
    args = files.args("--run-name", "d1", split="dev")
    del args[args.index("--output-dir") : args.index("--output-dir") + 2]

    result = CliRunner().invoke(app, args, obj=_no_db_deps(build_spec))

    assert result.exit_code == 0, result.output
    assert (tmp_path / "results" / "dev" / "d1.json").exists()


def test_v2_dev_runs_go_to_the_set_dev_directory(tmp_path, monkeypatch):
    monkeypatch.setattr("app.extraction.evaluation.cli.DEFAULT_RESULTS_DIR", tmp_path / "results")
    set_dir = tmp_path / "v2"
    set_dir.mkdir()
    files = _Files(set_dir, [_eval_case("1", "dev"), _eval_case("2", "test")])
    args = files.args("--run-name", "d2", split="dev")
    del args[args.index("--output-dir") : args.index("--output-dir") + 2]
    build_spec, _ = _build_spec(_haaland_out(), model="m")

    result = CliRunner().invoke(app, args, obj=_no_db_deps(build_spec))
    assert result.exit_code == 0, result.output
    assert (tmp_path / "results" / "v2" / "dev" / "d2.json").exists()

    args = files.args("--run-name", "t2", split="test")
    del args[args.index("--output-dir") : args.index("--output-dir") + 2]
    build_spec, _ = _build_spec(_haaland_out(), model="m")
    result = CliRunner().invoke(app, args, obj=_no_db_deps(build_spec))
    assert result.exit_code == 0, result.output
    assert (tmp_path / "results" / "v2" / "t2.json").exists()


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


def test_help_lists_every_evaluation_command():
    result = subprocess.run(
        [sys.executable, "-m", "app.extraction.evaluation", "--help"],
        capture_output=True,
        text=True,
        cwd=str(BACKEND_DIR),
    )
    assert result.returncode == 0
    for command in (
        "snapshot-players",
        "prelabel",
        "evaluate",
        "spend",
        "compare-labels",
        "review",
        "snapshot-fixtures",
        "export-judge-inputs",
        "estimate",
        "import-judge",
    ):
        assert command in result.stdout


@pytest.mark.parametrize(
    "args", [["prelabel", "--output", "out.jsonl"], ["evaluate", "--split", "dev"]]
)
def test_provider_option_removed(args):
    build_spec, _ = _build_spec()
    result = CliRunner().invoke(app, [*args, "--provider", "openai"], obj=_no_db_deps(build_spec))
    assert result.exit_code == 2


def _write_v2_set(set_dir, cases, tags=None):
    set_dir.mkdir(exist_ok=True)
    write_cases(set_dir / "cases.jsonl", cases)
    _snapshot(set_dir / "players.json")
    (set_dir / "fixtures.json").write_text(
        json.dumps(
            {
                "season": "2026/27",
                "fixtures": [
                    {
                        "season": "2026/27",
                        "fpl_id": 1,
                        "gameweek": 1,
                        "kickoff_at": (NOW + timedelta(days=2)).isoformat(),
                        "team_h_fpl_id": 1,
                        "team_a_fpl_id": 2,
                    }
                ],
            }
        )
    )
    (set_dir / "set.json").write_text(json.dumps({"version": "v2"}))


def _benched_case(case_id, split="test", tags=()):
    return _eval_case(case_id, split).model_copy(
        update={
            "tags": list(tags),
            "expected_events": []
            if "other_match_lineup" in tags
            else [
                ExpectedEvent(mention="Haaland", fpl_id=5, event_type="benched", certainty="likely")
            ],
        }
    )


def _benched_out() -> ExtractionOutput:
    return ExtractionOutput(
        events=[
            ExtractedEvent(player="Haaland", team=None, event_type="benched", certainty="likely")
        ]
    )


def test_evaluate_reads_the_set_directory_without_a_database(tmp_path, monkeypatch):
    monkeypatch.setattr("app.extraction.evaluation.cli.DEFAULT_RESULTS_DIR", tmp_path / "results")
    set_dir = tmp_path / "v2"
    _write_v2_set(
        set_dir,
        [_benched_case("1"), _benched_case("2", tags=["other_match_lineup"])],
    )
    # the first reply is the extraction, the second the match check of case 1, and so on
    from app.extraction.schemas import MatchCheck

    build_spec, _ = _build_spec(
        _benched_out(),
        MatchCheck(for_next_pl_match=True),
        _benched_out(),
        MatchCheck(for_next_pl_match=False),
        model="m",
    )

    result = CliRunner().invoke(
        app,
        ["evaluate", "--split", "test", "--model", "m", "--set", str(set_dir), "--run-name", "r"],
        obj=_no_db_deps(build_spec),
    )

    assert result.exit_code == 0, result.output
    assert "other match false alarms: 0 of 1 (0.000)" in result.stdout
    assert "line-ups dropped by the check: 0" in result.stdout
    data = json.loads((tmp_path / "results" / "v2" / "r.json").read_text())
    by_id = {c["id"]: c for c in data["case_results"]}
    assert by_id["1"]["predicted"][0]["event_type"] == "benched"
    assert by_id["2"]["predicted"] == []
    assert by_id["2"]["raw_predicted"][0]["for_next_pl_match"] is False
    assert by_id["2"]["other_match"] is True
    assert data["metrics"]["other_match_cases"] == 1


def test_evaluate_prints_the_absence_metrics(tmp_path, monkeypatch):
    monkeypatch.setattr("app.extraction.evaluation.cli.DEFAULT_RESULTS_DIR", tmp_path / "results")
    set_dir = tmp_path / "v3"
    case = _eval_case("1", "test").model_copy(update={"tags": ["other_match_absence"]})
    _write_v2_set(set_dir, [case.model_copy(update={"expected_events": []})])
    from app.extraction.schemas import MatchCheck

    build_spec, _ = _build_spec(_haaland_out(), MatchCheck(for_next_pl_match=False), model="m")

    result = CliRunner().invoke(
        app,
        ["evaluate", "--split", "test", "--model", "m", "--set", str(set_dir), "--run-name", "r"],
        obj=_no_db_deps(build_spec),
    )

    assert result.exit_code == 0, result.output
    assert "available F1: 0.000" in result.stdout
    assert "other match absence false alarms: 0 of 1" in result.stdout
    assert "injuries dropped by the check: 0" in result.stdout
    assert "other match absence check passed: yes" in result.stdout
    assert "injuries dropped check passed: yes" in result.stdout


def test_evaluate_header_names_the_three_prompts(tmp_path):
    set_dir = tmp_path / "set"
    _write_v2_set(set_dir, [_benched_case("1")])
    build_spec, _ = _build_spec(_haaland_out(), model="m")
    results = tmp_path / "results"

    result = CliRunner().invoke(
        app,
        [
            "evaluate",
            "--split",
            "test",
            "--model",
            "m",
            "--set",
            str(set_dir),
            "--run-name",
            "h",
            "--output-dir",
            str(results),
        ],
        obj=_no_db_deps(build_spec),
    )

    assert result.exit_code == 0, result.output
    header = json.loads((results / "h.json").read_text())["header"]
    assert header["set_version"] == "v2"
    for part in ("extraction@", "link_disambiguation@1", "match_check@2"):
        assert part in header["prompt_version"]


def test_snapshot_fixtures_writes_the_season(db, tmp_path):
    with Session(db) as session:
        _seed_players(session, "2025/26", 1, "Old")
        _seed_players(session, "2026/27", 2, "Current")
        session.add(Team(season="2026/27", fpl_id=2, name="Team Two", short_name="TWO"))
        session.flush()
        for season, fpl_id in (("2025/26", 1), ("2026/27", 2)):
            session.add(
                Fixture(
                    season=season,
                    fpl_id=fpl_id,
                    kickoff_at=NOW,
                    team_h_fpl_id=1,
                    team_a_fpl_id=1 if season == "2025/26" else 2,
                    finished=False,
                )
            )
        session.commit()
    build_spec, _ = _build_spec()

    result = CliRunner().invoke(
        app, ["snapshot-fixtures", "--set", str(tmp_path)], obj=_deps(db, build_spec)
    )

    assert result.exit_code == 0, result.output
    fixtures = load_fixtures_snapshot(tmp_path / "fixtures.json")
    assert [(f.season, f.fpl_id, f.kickoff_at) for f in fixtures] == [("2026/27", 2, NOW)]


def test_compare_labels_lists_each_changed_case(tmp_path):
    reviewed, baseline = _reviewed_and_baseline()
    cases, base = tmp_path / "cases.jsonl", tmp_path / "base.jsonl"
    write_cases(cases, reviewed)
    write_cases(base, baseline)

    result = CliRunner().invoke(
        app, ["compare-labels", "--set", str(tmp_path), "--baseline", str(base)]
    )

    assert result.exit_code == 0, result.output
    assert "changed a: relabelled Salah certainty" in result.stdout
    assert "changed b: added 1" in result.stdout
    assert "changed c: removed 1" in result.stdout


def test_estimate_prints_calls_and_tokens(tmp_path):
    set_dir = tmp_path / "set"
    _write_v2_set(set_dir, [_benched_case("1")])
    out = tmp_path / "export"

    exported = CliRunner().invoke(
        app,
        [
            "export-judge-inputs",
            "--set",
            str(set_dir),
            "--cases",
            str(set_dir / "cases.jsonl"),
            "--out",
            str(out),
        ],
    )
    estimated = CliRunner().invoke(app, ["estimate", "--export", str(out)])

    assert exported.exit_code == 0, exported.output
    assert "exported 1 inputs" in exported.stdout
    assert estimated.exit_code == 0, estimated.output
    assert "estimate: 1 calls, about" in estimated.stdout
    missing = CliRunner().invoke(app, ["estimate", "--export", str(tmp_path / "none")])
    assert missing.exit_code == 1


def test_export_with_availability_keywords(tmp_path):
    set_dir = tmp_path / "set"
    _write_v2_set(set_dir, [_benched_case("1")])
    corpus = tmp_path / "corpus.jsonl"
    rows = [
        {"x_id": 11, "text": "Palmer is NOT suspended"},
        {"x_id": 12, "text": "the weather is nice"},
    ]
    corpus.write_text(
        "\n".join(
            json.dumps(
                {
                    "author_handle": "reporter",
                    "created_at": NOW.isoformat(),
                    "is_repost": False,
                    "is_reply": False,
                    "is_source": True,
                    **row,
                }
            )
            for row in rows
        )
    )

    def export(*flags):
        return CliRunner().invoke(
            app,
            ["export-judge-inputs", "--set", str(set_dir), "--corpus", str(corpus), *flags]
            + ["--out", str(tmp_path / f"export{len(flags)}")],
        )

    plain = export("--keywords")
    both = export("--keywords", "--availability-keywords")

    assert plain.exit_code == 0 and "exported 0 inputs" in plain.stdout
    assert both.exit_code == 0, both.output
    assert "exported 1 inputs" in both.stdout


def test_export_and_import_through_the_cli(tmp_path):
    set_dir = tmp_path / "set"
    _write_v2_set(set_dir, [_benched_case("1")])
    out = tmp_path / "export"
    runner = CliRunner()

    runner.invoke(
        app,
        [
            "export-judge-inputs",
            "--set",
            str(set_dir),
            "--cases",
            str(set_dir / "cases.jsonl"),
            "--out",
            str(out),
        ],
    )
    (out / "verdicts").mkdir()
    (out / "verdicts" / "I0001.json").write_text(
        json.dumps({"events": [], "other_match_lineup": True})
    )
    target = tmp_path / "v2"
    target.mkdir()
    for name in ("players.json", "fixtures.json"):
        (target / name).write_text((set_dir / name).read_text())

    result = runner.invoke(
        app,
        ["import-judge", "--export", str(out), "--set", str(target), "--judge-model", "j"],
    )

    assert result.exit_code == 0, result.output
    assert "v1 cases changed (to review): 1" in result.stdout
    refused = runner.invoke(app, ["export-judge-inputs", "--set", str(set_dir), "--out", str(out)])
    assert refused.exit_code == 1


def test_prelabel_leaves_out_a_lineup_the_check_answers_false(db, tmp_path):
    with Session(db) as session:
        _seed_haaland(session)
        session.add(Team(season="2026/27", fpl_id=2, name="Arsenal", short_name="ARS"))
        session.flush()
        session.add(
            Fixture(
                season="2026/27",
                fpl_id=1,
                kickoff_at=NOW + timedelta(days=1),
                team_h_fpl_id=1,
                team_a_fpl_id=2,
                finished=False,
            )
        )
        session.add(_tweet(10))
        session.commit()
    output_with_lineup = ExtractionOutput(
        events=[
            ExtractedEvent(player="Haaland", team=None, event_type="benched", certainty="likely"),
            ExtractedEvent(player="Haaland", team=None, event_type="available", certainty="likely"),
        ]
    )
    build_spec, _ = _build_spec(output_with_lineup, MatchCheck(for_next_pl_match=False))
    output = tmp_path / "cases.jsonl"

    result = CliRunner().invoke(
        app, ["prelabel", "--output", str(output)], obj=_deps(db, build_spec)
    )

    assert result.exit_code == 0, result.output
    [case] = load_cases(output)
    assert [(e.fpl_id, e.event_type) for e in case.expected_events] == [(5, "available")]


def test_prelabel_needs_a_set_or_an_output(tmp_path):
    build_spec, _ = _build_spec()

    result = CliRunner().invoke(app, ["prelabel"], obj=_no_db_deps(build_spec))

    assert result.exit_code == 1
    assert "pass --set or --output" in result.stderr


@pytest.mark.parametrize("command", ["snapshot-players", "snapshot-fixtures"])
def test_snapshots_need_a_set(command):
    build_spec, _ = _build_spec()

    result = CliRunner().invoke(app, [command], obj=_no_db_deps(build_spec))

    assert result.exit_code == 2
    assert "--set" in re.sub(r"\x1b\[[0-9;]*m", "", result.output)


def test_snapshot_players_refuses_an_existing_file_without_force(db, tmp_path):
    with Session(db) as session:
        _seed_players(session, "2026/27", 2, "Current")
    build_spec, _ = _build_spec()
    output = tmp_path / "players.json"
    output.write_text("reviewed snapshot\n")

    refused = CliRunner().invoke(
        app, ["snapshot-players", "--set", str(tmp_path)], obj=_deps(db, build_spec)
    )

    assert refused.exit_code == 1
    assert "pass --force" in refused.stderr
    assert output.read_text() == "reviewed snapshot\n"

    forced = CliRunner().invoke(
        app, ["snapshot-players", "--set", str(tmp_path), "--force"], obj=_deps(db, build_spec)
    )

    assert forced.exit_code == 0, forced.output
    assert [p.web_name for p in load_snapshot(output)[0]] == ["Current"]


def test_compare_labels_needs_a_baseline_for_a_set_other_than_v1(tmp_path):
    reviewed, _ = _reviewed_and_baseline()
    set_dir = tmp_path / "v2"
    set_dir.mkdir()
    write_cases(set_dir / "cases.jsonl", reviewed)
    (set_dir / "set.json").write_text(json.dumps({"version": "v2"}))

    result = CliRunner().invoke(app, ["compare-labels", "--set", str(set_dir)])

    assert result.exit_code == 1
    assert "pass --revision or --baseline" in result.stderr


def test_compare_labels_defaults_to_the_v1_prelabel_revision(tmp_path):
    reviewed, _ = _reviewed_and_baseline()
    set_dir = tmp_path / "v1"
    set_dir.mkdir()
    write_cases(set_dir / "cases.jsonl", reviewed)
    subprocess.run(["git", "init", "-q"], cwd=set_dir, check=True, capture_output=True)

    result = CliRunner().invoke(app, ["compare-labels", "--set", str(set_dir)])

    assert result.exit_code == 1
    assert f"cannot read cases.jsonl at revision {V1_BASELINE_REVISION}" in result.stderr
