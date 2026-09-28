import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Protocol

import typer

from app.core.errors import ConfigError
from app.core.settings import TweetSettings
from app.tweets.config import check_source
from app.tweets.measure import (
    LatencyRecord,
    PollCounts,
    format_summary,
    poll_counts_to_json,
    read_records,
    record_to_json,
    summarise,
)
from app.tweets.sources import SOURCE_NAMES, build_source
from app.tweets.sources.base import TweetSource
from app.tweets.sources.paging import collect_new

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Side-by-side tweet source latency measurement.",
)


class Clock(Protocol):
    def now(self) -> datetime: ...

    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


@dataclass(frozen=True)
class MeasureDeps:
    settings: TweetSettings
    build_source: Callable[[str, TweetSettings], TweetSource] = build_source
    clock_factory: Callable[[], Clock] = SystemClock


def get_deps(ctx: typer.Context) -> MeasureDeps:
    if ctx.obj is None:  # tests inject their own
        ctx.obj = MeasureDeps(settings=TweetSettings())
    return ctx.obj


def _list_id(settings: TweetSettings) -> int:
    if not settings.x_list_id or not settings.x_list_id.isdigit():
        raise ConfigError("X_LIST_ID must be set to the numeric ID of the watched X List")
    return int(settings.x_list_id)


def _measure_one(
    name: str,
    make_source: Callable[[], TweetSource],
    list_id: int,
    interval_seconds: float,
    start: datetime,
    deadline: datetime,
    clock: Clock,
    records: list[LatencyRecord],
    records_lock: threading.Lock,
) -> PollCounts:
    source = make_source()
    since_id: int | None = None
    polls = 0
    failed_polls = 0
    try:
        while clock.now() < deadline:
            polls += 1
            try:
                posts = collect_new(source, list_id, since_id)
                fetched_at = clock.now()
                for post in posts:
                    since_id = post.x_id if since_id is None else max(since_id, post.x_id)
                    if post.created_at >= start:
                        with records_lock:
                            records.append(
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

    start = deps.clock_factory().now()
    deadline = start + timedelta(minutes=duration_minutes)
    records: list[LatencyRecord] = []
    records_lock = threading.Lock()
    poll_counts: dict[str, PollCounts] = {}
    poll_counts_lock = threading.Lock()

    def run_source(name: str) -> None:
        counts = _measure_one(
            name,
            lambda: deps.build_source(name, deps.settings),
            list_id,
            interval_seconds,
            start,
            deadline,
            deps.clock_factory(),
            records,
            records_lock,
        )
        with poll_counts_lock:
            poll_counts[name] = counts

    threads = [
        threading.Thread(target=run_source, args=(name,), name=f"measure-{name}") for name in active
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    lines = [record_to_json(record) for record in sorted(records, key=lambda r: (r.source, r.x_id))]
    for name in active:
        counts = poll_counts.get(name, PollCounts(name, polls=0, failed_polls=0))
        lines.append(poll_counts_to_json(counts))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    summaries = summarise(records, [poll_counts[name] for name in active if name in poll_counts])
    typer.echo(format_summary(summaries, markdown=False))


@app.command(help="Print a per-source summary of a measurement file written by `measure`.")
def summary(
    path: Path,
    markdown: bool = typer.Option(False, "--markdown"),
) -> None:
    records, poll_counts = read_records(path)
    typer.echo(format_summary(summarise(records, poll_counts), markdown=markdown))


def main() -> None:
    app(prog_name="python -m app.tweets")
