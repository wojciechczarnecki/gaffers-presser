import os
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta

from sqlmodel import Session
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
from app.tweets.membership import latest_snapshot
from app.tweets.sources.base import MembershipNotSupportedError
from tests.tweets.fakes import FakeSource, post

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

    def members(self, list_id: int):
        raise MembershipNotSupportedError("fake: membership not supported")

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


def _measure(deps: MeasureDeps, output, *args: str):
    return CliRunner().invoke(
        app,
        ["measure", "--interval-seconds", "20", "--output", str(output), *args],
        obj=deps,
    )


def test_measure_writes_one_record_per_source_and_post(tmp_path):
    post_new = post(
        2001, author_handle="synthetic_leaker_1", created_at=START + timedelta(seconds=5)
    )
    post_late = post(2000, created_at=START + timedelta(seconds=10))
    post_old = post(1000, created_at=START - timedelta(seconds=5))
    src_a = ScriptedSource(
        "twscrape", [[[post_old]], [[post_new, post_old]], [[post_new, post_late, post_old]]]
    )
    src_b = ScriptedSource("x_api", [[[]], [[]], [[post_new]]])
    sources = {"twscrape": src_a, "x_api": src_b}

    deps = MeasureDeps(
        settings=_settings(),
        build_source=lambda name, settings: sources[name],
        clock_factory=lambda stop: SeqClock(START),
    )

    output = tmp_path / "latency.jsonl"
    result = _measure(
        deps, output, "--duration-minutes", "1", "--source", "twscrape", "--source", "x_api"
    )

    assert result.exit_code == 0, result.output
    records, poll_counts = read_records(output)
    by_key = {(r.source, r.x_id): r for r in records}
    assert len(records) == len(by_key) == 3
    assert ("twscrape", 1000) not in by_key  # older than the measurement start

    twscrape_new = by_key[("twscrape", 2001)]
    assert twscrape_new.author_handle == "synthetic_leaker_1"
    assert twscrape_new.created_at == START + timedelta(seconds=5)
    assert twscrape_new.first_fetched_at == START + timedelta(seconds=20)
    assert twscrape_new.latency_seconds == 15.0

    # A post that became visible after a newer one is still measured.
    twscrape_late = by_key[("twscrape", 2000)]
    assert twscrape_late.first_fetched_at == START + timedelta(seconds=40)
    assert twscrape_late.latency_seconds == 30.0

    x_api_new = by_key[("x_api", 2001)]
    assert x_api_new.first_fetched_at == START + timedelta(seconds=40)
    assert x_api_new.latency_seconds == 35.0

    assert src_a.closed and src_b.closed
    counts_by_source = {c.source: c for c in poll_counts}
    assert counts_by_source["twscrape"] == PollCounts("twscrape", polls=3, failed_polls=0)
    assert counts_by_source["x_api"] == PollCounts("x_api", polls=3, failed_polls=0)


def test_measure_writes_each_record_as_it_is_recorded(tmp_path):
    output = tmp_path / "latency.jsonl"
    seen_on_second_poll: list[str] = []

    class PeekingSource(ScriptedSource):
        def pages(self, list_id: int):
            if self._i == 1:
                seen_on_second_poll.append(output.read_text(encoding="utf-8"))
            return super().pages(list_id)

    src = PeekingSource("twscrape", [[[post(2001, created_at=START)]], [[]]])
    deps = MeasureDeps(
        settings=_settings(),
        build_source=lambda name, settings: src,
        clock_factory=lambda stop: SeqClock(START),
    )

    result = _measure(deps, output, "--duration-minutes", "0.5", "--source", "twscrape")

    assert result.exit_code == 0, result.output
    assert len(seen_on_second_poll) == 1
    assert '"x_id": "2001"' in seen_on_second_poll[0]


def test_measure_counts_a_failed_source_build_and_retries(tmp_path):
    src = ScriptedSource("twscrape", [[]])
    calls = {"n": 0}

    def build(name, settings):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return src

    deps = MeasureDeps(
        settings=_settings(), build_source=build, clock_factory=lambda stop: SeqClock(START)
    )

    output = tmp_path / "latency.jsonl"
    result = _measure(deps, output, "--duration-minutes", "1", "--source", "twscrape")

    assert result.exit_code == 0, result.output
    assert "twscrape: source build failed: RuntimeError" in result.output
    _records, poll_counts = read_records(output)
    assert poll_counts == [PollCounts("twscrape", polls=3, failed_polls=1)]
    assert src.closed


def test_measure_stops_and_keeps_records_on_ctrl_c(tmp_path):
    class InterruptingSource(ScriptedSource):
        def pages(self, list_id: int):
            if self._i == 1:
                os.kill(os.getpid(), signal.SIGINT)
            return super().pages(list_id)

    class StopWaitingClock(SeqClock):
        def __init__(self, start: datetime, stop: threading.Event) -> None:
            super().__init__(start)
            self._stop = stop

        def sleep(self, seconds: float) -> None:
            self._stop.wait(timeout=0.01)
            super().sleep(seconds)

    src = InterruptingSource("twscrape", [[[post(2001, created_at=START)]], [[]]])
    deps = MeasureDeps(
        settings=_settings(),
        build_source=lambda name, settings: src,
        clock_factory=lambda stop: StopWaitingClock(START, stop),
    )

    output = tmp_path / "latency.jsonl"
    started = time.monotonic()
    result = _measure(deps, output, "--duration-minutes", "60", "--source", "twscrape")

    assert result.exit_code == 0, result.output
    assert time.monotonic() - started < 5, result.output
    assert "interrupted" in result.output
    records, poll_counts = read_records(output)
    assert [r.x_id for r in records] == [2001]
    assert poll_counts == [PollCounts("twscrape", polls=2, failed_polls=0)]
    assert src.closed


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
        clock_factory=lambda stop: SeqClock(START),
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
        clock_factory=lambda stop: SeqClock(START),
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
    assert "members" in result.stdout


def _members_deps(db, source, **overrides) -> MeasureDeps:
    return MeasureDeps(
        settings=_settings(tweet_source="twscrape", **overrides),
        build_source=lambda name, settings: source,
        clock_factory=lambda stop: SeqClock(START),
        make_engine=lambda: db,
    )


def test_members_command_stores_and_prints_snapshot(db):
    source = FakeSource([], members=["Synthetic_A", "synthetic_b"])

    result = CliRunner().invoke(app, ["members"], obj=_members_deps(db, source))

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "List members: 2  snapshot: 2026-09-28 12:00:00 UTC"
    with Session(db) as session:
        snapshot = latest_snapshot(session)
    assert snapshot.handles == frozenset({"synthetic_a", "synthetic_b"})
    assert snapshot.fetched_at == START
    assert source.closed


def test_members_command_unsupported_source(db):
    source = FakeSource([])

    result = CliRunner().invoke(app, ["members"], obj=_members_deps(db, source))

    assert result.exit_code == 0, result.output
    assert "not supported by fake" in result.output
    with Session(db) as session:
        assert latest_snapshot(session).handles is None


def test_members_command_failure_exits_1(db):
    source = FakeSource([], members=RuntimeError("secret-token-xyz"))

    result = CliRunner().invoke(app, ["members"], obj=_members_deps(db, source))

    assert result.exit_code == 1
    assert "error: list membership fetch failed: RuntimeError" in result.output
    assert "secret-token-xyz" not in result.output
    assert source.closed


def test_members_command_needs_tweet_source_and_list_id(db):
    source = FakeSource([], members=["a"])
    no_source = MeasureDeps(settings=_settings(), make_engine=lambda: db)
    result = CliRunner().invoke(app, ["members"], obj=no_source)
    assert result.exit_code == 1
    assert "TWEET_SOURCE" in result.output

    result = CliRunner().invoke(app, ["members"], obj=_members_deps(db, source, x_list_id=""))
    assert result.exit_code == 1
    assert "X_LIST_ID" in result.output
