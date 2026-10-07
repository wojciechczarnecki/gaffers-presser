import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Protocol, TextIO

import typer
from sqlalchemy import Engine

from app.core.errors import CollectorError, ConfigError
from app.core.settings import TweetSettings, load_settings
from app.db.engine import make_engine
from app.tweets.config import check_source, resolve_ingest
from app.tweets.measure import (
    LatencyRecord,
    PollCounts,
    format_summary,
    poll_counts_to_json,
    read_records,
    record_to_json,
    summarise,
)
from app.tweets.membership import MembershipShrunkError, fetch_membership, store_membership
from app.tweets.sources import SOURCE_NAMES, build_source
from app.tweets.sources.base import MembershipNotSupportedError, TweetSource
from app.tweets.sources.paging import collect_new

_STOP_TIMEOUT_SECONDS = 15.0
_JOIN_POLL_SECONDS = 0.2

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Side-by-side tweet source latency measurement.",
)


class Clock(Protocol):
    def now(self) -> datetime: ...

    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def __init__(self, stop_event: threading.Event) -> None:
        self._stop_event = stop_event

    def now(self) -> datetime:
        return datetime.now(UTC)

    def sleep(self, seconds: float) -> None:
        self._stop_event.wait(seconds)


@dataclass(frozen=True)
class MeasureDeps:
    settings: TweetSettings
    build_source: Callable[[str, TweetSettings], TweetSource] = build_source
    clock_factory: Callable[[threading.Event], Clock] = SystemClock
    make_engine: Callable[[], Engine] | None = None


def get_deps(ctx: typer.Context) -> MeasureDeps:
    if ctx.obj is None:  # tests inject their own
        ctx.obj = MeasureDeps(settings=TweetSettings())
    return ctx.obj


def _list_id(settings: TweetSettings) -> int:
    if not settings.x_list_id or not settings.x_list_id.isdigit():
        raise ConfigError("X_LIST_ID must be set to the numeric ID of the watched X List")
    return int(settings.x_list_id)


class RecordWriter:
    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self._lock = threading.Lock()
        self.records: list[LatencyRecord] = []

    def write(self, record: LatencyRecord) -> None:
        with self._lock:
            self.records.append(record)
            self._write_line(record_to_json(record))

    def write_counts(self, counts: PollCounts) -> None:
        with self._lock:
            self._write_line(poll_counts_to_json(counts))

    def _write_line(self, line: str) -> None:
        self._stream.write(line + "\n")
        self._stream.flush()


def _measure_one(
    name: str,
    make_source: Callable[[], TweetSource],
    list_id: int,
    interval_seconds: float,
    start: datetime,
    deadline: datetime,
    clock: Clock,
    stop_event: threading.Event,
    writer: RecordWriter,
) -> PollCounts:
    source: TweetSource | None = None
    member_handles: set[str] | None = None
    members_checked = False
    since_id: int | None = None
    seen: set[int] = set()
    polls = 0
    failed_polls = 0
    build_error_reported = False
    try:
        while clock.now() < deadline and not stop_event.is_set():
            polls += 1
            try:
                if source is None:
                    try:
                        source = make_source()
                    except Exception as exc:
                        if not build_error_reported:
                            build_error_reported = True
                            typer.echo(
                                f"{name}: source build failed: {type(exc).__name__}", err=True
                            )
                        raise
                if not members_checked:
                    members_checked = True
                    try:
                        member_handles = {h.lower() for h in source.members(list_id)}
                    except MembershipNotSupportedError:
                        member_handles = None
                    except CollectorError as exc:
                        member_handles = None
                        typer.echo(
                            f"{name}: list members unavailable, counting every timeline post:"
                            f" {type(exc).__name__}",
                            err=True,
                        )
                posts = collect_new(source, list_id, since_id)
                fetched_at = clock.now()
                for post in posts:
                    if post.embedded:
                        continue
                    since_id = post.x_id if since_id is None else max(since_id, post.x_id)
                    if post.x_id in seen:
                        continue
                    seen.add(post.x_id)
                    if member_handles is not None and post.author_handle.lower() not in (
                        member_handles
                    ):
                        continue
                    if post.created_at >= start:
                        writer.write(
                            LatencyRecord(
                                source=name,
                                x_id=post.x_id,
                                author_handle=post.author_handle,
                                created_at=post.created_at,
                                first_fetched_at=fetched_at,
                                latency_seconds=(fetched_at - post.created_at).total_seconds(),
                            )
                        )
            except Exception:
                failed_polls += 1
            clock.sleep(interval_seconds)
    finally:
        if source is not None:
            source.close()
    return PollCounts(source=name, polls=polls, failed_polls=failed_polls)


SourcesOption = Annotated[
    list[str] | None,
    typer.Option("--source", help="Measure only these sources (default: all configured sources)."),
]


@app.command(help="Poll every configured source side by side and write per-post latency.")
def measure(
    ctx: typer.Context,
    output: Annotated[Path, typer.Option("--output")],
    interval_seconds: Annotated[float, typer.Option("--interval-seconds")] = 20.0,
    duration_minutes: Annotated[float, typer.Option("--duration-minutes")] = 60.0,
    source: SourcesOption = None,
) -> None:
    deps = get_deps(ctx)
    source = source or []
    try:
        list_id = _list_id(deps.settings)
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None

    names = list(source) if source else list(SOURCE_NAMES)
    active: list[str] = []
    for name in names:
        try:
            check_source(deps.settings, name)
        except ConfigError as exc:
            typer.echo(f"skipped {name}: {exc}")
            continue
        active.append(name)

    stop_event = threading.Event()
    start = deps.clock_factory(stop_event).now()
    deadline = start + timedelta(minutes=duration_minutes)
    poll_counts: dict[str, PollCounts] = {}
    poll_counts_lock = threading.Lock()

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as stream:
        writer = RecordWriter(stream)

        def run_source(name: str, done: threading.Event) -> None:
            try:
                counts = _measure_one(
                    name,
                    lambda: deps.build_source(name, deps.settings),
                    list_id,
                    interval_seconds,
                    start,
                    deadline,
                    deps.clock_factory(stop_event),
                    stop_event,
                    writer,
                )
                with poll_counts_lock:
                    poll_counts[name] = counts
            finally:
                done.set()

        done_events = {name: threading.Event() for name in active}
        for name in active:
            threading.Thread(
                target=run_source,
                args=(name, done_events[name]),
                name=f"measure-{name}",
                daemon=True,
            ).start()
        try:
            # Short waits let the main thread run the SIGINT handler even when the signal
            # lands on a measuring thread.
            for done in done_events.values():
                while not done.wait(timeout=_JOIN_POLL_SECONDS):
                    pass
        except KeyboardInterrupt:
            typer.echo("interrupted: stopping the measurement", err=True)
            stop_event.set()
            for done in done_events.values():
                done.wait(timeout=_STOP_TIMEOUT_SECONDS)

        with poll_counts_lock:
            finished = [poll_counts[name] for name in active if name in poll_counts]
        for counts in finished:
            writer.write_counts(counts)
        records = list(writer.records)

    typer.echo(format_summary(summarise(records, finished), markdown=False))


@app.command(help="Print a per-source summary of a measurement file written by `measure`.")
def summary(
    path: Path,
    markdown: bool = typer.Option(False, "--markdown"),
) -> None:
    records, poll_counts = read_records(path)
    typer.echo(format_summary(summarise(records, poll_counts), markdown=markdown))


@app.command(help="Fetch the watched List's members now and store the snapshot.")
def members(
    ctx: typer.Context,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Store the snapshot even when it drops more members than the poller accepts.",
        ),
    ] = False,
) -> None:
    deps = get_deps(ctx)
    try:
        config = resolve_ingest(deps.settings)
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    if config is None:
        typer.echo("error: TWEET_SOURCE must be set to fetch the List's members", err=True)
        raise typer.Exit(1)

    try:
        engine = (
            deps.make_engine() if deps.make_engine else make_engine(load_settings().database_url)
        )
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    now = deps.clock_factory(threading.Event()).now()
    source = None
    try:
        source = deps.build_source(config.source_name, deps.settings)
        snapshot = fetch_membership(source, config.list_id, now)
    except Exception as exc:
        typer.echo(f"error: list membership fetch failed: {type(exc).__name__}", err=True)
        raise typer.Exit(1) from None
    finally:
        if source is not None:
            source.close()
    try:
        store_membership(engine, snapshot, force=force)
    except MembershipShrunkError as exc:
        typer.echo(f"error: snapshot refused, {exc}; rerun with --force to store it", err=True)
        raise typer.Exit(1) from None
    except Exception as exc:
        typer.echo(f"error: list membership save failed: {type(exc).__name__}", err=True)
        raise typer.Exit(1) from None

    if snapshot.handles is None:
        typer.echo(
            f"List members: not supported by {snapshot.source} (every post counts as a list post)"
        )
        return
    stamp = snapshot.fetched_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    typer.echo(f"List members: {len(snapshot.handles)}  snapshot: {stamp}")


def main() -> None:
    app(prog_name="python -m app.tweets")
