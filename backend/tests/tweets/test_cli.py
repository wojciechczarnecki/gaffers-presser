import subprocess
import sys
from datetime import UTC, datetime, timedelta

from typer.testing import CliRunner

from app.core.settings import TweetSettings
from app.tweets.cli import MeasureDeps, app
from app.tweets.measure import (
    LatencyRecord,
    PollCounts,
    poll_counts_to_json,
    read_records,
    record_to_json,
)
from tests.tweets.fakes import post

START = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)


class ScriptedSource:
    def __init__(self, name: str, scripts: list) -> None:
        self.name = name
        self.max_pages = 5
        self._scripts = list(scripts)
        self._i = 0
        self.closed = False

    def pages(self, list_id: int):
        script = self._scripts[min(self._i, len(self._scripts) - 1)]
        self._i += 1
        if isinstance(script, Exception):
            raise script
        return iter(script)

    def close(self) -> None:
        self.closed = True


class SeqClock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


def _settings(**overrides) -> TweetSettings:
    fields = {
        "_env_file": None,
        "x_list_id": "123",
        "twscrape_username": "tester",
        "twscrape_cookies": "auth_token=a; ct0=b",
        "x_api_bearer_token": "tok",
    }
    fields.update(overrides)
    return TweetSettings(**fields)


def test_measure_writes_one_record_per_source_and_post(tmp_path):
    post_new = post(2001, created_at=START + timedelta(seconds=5))
    post_old = post(1000, created_at=START - timedelta(seconds=5))
    src_a = ScriptedSource("twscrape", [[[post_old, post_new]], [[post_new]], []])
    src_b = ScriptedSource("x_api", [[[post_new]], [], []])
    sources = {"twscrape": src_a, "x_api": src_b}

    deps = MeasureDeps(
        settings=_settings(),
        build_source=lambda name, settings: sources[name],
        clock_factory=lambda: SeqClock(START),
    )

    output = tmp_path / "latency.jsonl"
    result = CliRunner().invoke(
        app,
        [
            "measure",
            "--interval-seconds",
            "20",
            "--duration-minutes",
            "1",
            "--output",
            str(output),
            "--source",
            "twscrape",
            "--source",
            "x_api",
        ],
        obj=deps,
    )

    assert result.exit_code == 0, result.output
    records, poll_counts = read_records(output)
    keys = {(r.source, r.x_id) for r in records}
    assert ("twscrape", 2001) in keys
    assert ("x_api", 2001) in keys
    assert ("twscrape", 1000) not in keys  # older than the measurement start
    assert len([r for r in records if r.x_id == 2001 and r.source == "twscrape"]) == 1
    assert src_a.closed and src_b.closed
    counts_by_source = {c.source: c for c in poll_counts}
    assert counts_by_source["twscrape"].polls == 3
    assert counts_by_source["twscrape"].failed_polls == 0


def test_measure_skips_source_without_credentials(tmp_path):
    src = ScriptedSource("twitterapi_io", [[]])
    deps = MeasureDeps(
        settings=_settings(
            twscrape_username="",
            twscrape_cookies=None,
            x_api_bearer_token=None,
            twitterapi_io_key="key",
        ),
        build_source=lambda name, settings: src,
        clock_factory=lambda: SeqClock(START),
    )

    output = tmp_path / "latency.jsonl"
    result = CliRunner().invoke(
        app,
        [
            "measure",
            "--interval-seconds",
            "20",
            "--duration-minutes",
            "0.1",
            "--output",
            str(output),
        ],
        obj=deps,
    )

    assert result.exit_code == 0, result.output
    assert "skipped twscrape:" in result.output
    assert "TWSCRAPE_USERNAME" in result.output
    assert "skipped x_api:" in result.output
    assert "X_API_BEARER_TOKEN" in result.output
    assert "skipped twitterapi_io" not in result.output


def test_measure_counts_failed_polls_and_still_finishes(tmp_path):
    src = ScriptedSource("twscrape", [RuntimeError("boom"), RuntimeError("boom")])
    deps = MeasureDeps(
        settings=_settings(),
        build_source=lambda name, settings: src,
        clock_factory=lambda: SeqClock(START),
    )

    output = tmp_path / "latency.jsonl"
    result = CliRunner().invoke(
        app,
        [
            "measure",
            "--interval-seconds",
            "20",
            "--duration-minutes",
            "0.5",
            "--output",
            str(output),
            "--source",
            "twscrape",
        ],
        obj=deps,
    )

    assert result.exit_code == 0, result.output
    _records, poll_counts = read_records(output)
    counts = poll_counts[0]
    assert counts.polls >= 1
    assert counts.failed_polls == counts.polls


def test_summary_command_reads_a_measurement_file(tmp_path):
    path = tmp_path / "latency.jsonl"
    record = record_to_json(
        LatencyRecord(
            source="twscrape",
            x_id=1,
            author_handle="synthetic_leaker",
            created_at=START,
            first_fetched_at=START + timedelta(seconds=10),
            latency_seconds=10.0,
        )
    )
    counts = poll_counts_to_json(PollCounts(source="twscrape", polls=1, failed_polls=0))
    path.write_text(record + "\n" + counts + "\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["summary", str(path)])

    assert result.exit_code == 0, result.output
    assert "twscrape" in result.output
    assert "10.0" in result.output


def test_summary_command_markdown(tmp_path):
    path = tmp_path / "latency.jsonl"
    path.write_text('{"source": "twscrape", "failed_polls": 0, "polls": 1}\n', encoding="utf-8")

    result = CliRunner().invoke(app, ["summary", str(path), "--markdown"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0].startswith("| source |")


def test_help_via_subprocess():
    result = subprocess.run(
        [sys.executable, "-m", "app.tweets", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "measure" in result.stdout
    assert "summary" in result.stdout
