from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

import typer
from sqlalchemy import Engine
from sqlmodel import Session

from app.core.clock import Clock, SystemClock
from app.core.errors import ConfigError
from app.core.local_time import format_local, parse_local
from app.core.settings import load_settings
from app.corroboration.config import CorroborationSettings
from app.corroboration.runtime import build_runtime
from app.corroboration.schemas import Citation, Corroboration, PlayerRef
from app.corroboration.service import CorroborationRuntime, corroborate
from app.corroboration.sources import Aliases, resolve_player
from app.db.engine import make_engine

app = typer.Typer(
    add_completion=False,
    help="Corroborate the latest news about one player, at any past moment.",
)


@dataclass(frozen=True)
class CorroborationCliDeps:
    engine: Engine | None
    settings: CorroborationSettings
    clock: Clock
    make_runtime: Callable[[], CorroborationRuntime]
    aliases: Aliases | None = None


def fail(message: str) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(1)


def _deps_from_settings() -> CorroborationCliDeps:
    from app.llm.settings import load_llm_settings

    try:
        settings = load_llm_settings(CorroborationSettings)
        engine = make_engine(load_settings().database_url)
    except ConfigError as exc:
        raise fail(str(exc)) from None
    clock = SystemClock()
    return CorroborationCliDeps(
        engine=engine,
        settings=settings,
        clock=clock,
        make_runtime=lambda: build_runtime(settings, clock),
    )


def get_deps(ctx: typer.Context) -> CorroborationCliDeps:
    if ctx.obj is None:  # tests inject their own
        ctx.obj = _deps_from_settings()
    return ctx.obj


def _parse(value: str | None, option: str) -> datetime | None:
    if value is None:
        return None
    try:
        return parse_local(value)
    except ValueError:
        raise fail(f"{option} must be YYYY-MM-DD or YYYY-MM-DDTHH:MM") from None


def _account(author: str, original: str | None) -> str:
    if original:
        return f"@{author} (repost of @{original})"
    return f"@{author}"


def _echo_group(title: str, citations: list[Citation]) -> None:
    typer.echo(f"{title} ({len(citations)}):")
    for freshness in ("new", "context"):
        chosen = [c for c in citations if c.freshness == freshness]
        if not chosen:
            continue
        typer.echo(f"  {freshness}:")
        for citation in chosen:
            certainty = f", {citation.certainty}" if citation.certainty else ""
            typer.echo(
                f"    - {_account(citation.author_handle, citation.reposted_author_handle)}"
                f"  {format_local(citation.created_at)}"
                f"  [{citation.origin}{certainty}]  {citation.url}"
            )
            typer.echo(f"        {' '.join(citation.text.split())}")


def _retrieval_line(result: Corroboration) -> str:
    report = result.retrieval
    if report.status == "skipped":
        return f"Retrieval and judge: skipped ({report.skipped_reason})"
    line = f"Retrieval: ran; judged {report.judged}, unjudged {report.unjudged}"
    if report.failed_legs:
        line += (
            f"; the {', '.join(report.failed_legs)} leg failed ({report.failure}), full-text only"
        )
    elif report.failure:
        line += f"; {report.failure}"
    return line


def _echo_result(result: Corroboration) -> None:
    player = result.player
    team = f" ({player.team_name})" if player.team_name else ""
    typer.echo(f"Player: {player.web_name}{team}, FPL ID {player.fpl_id}")
    typer.echo(
        f"Window: {format_local(result.window_start)} to {format_local(result.as_of)}"
        f" (Europe/Warsaw); new since {format_local(result.new_since)}"
    )
    anchor = result.anchor
    if anchor is None or result.grade is None:
        typer.echo("No claim about this player in the window.")
        return
    typer.echo(
        f"Anchor: {anchor.event_type} ({anchor.certainty}) by "
        f"{_account(anchor.post.author_handle, anchor.post.reposted_author_handle)}"
        f" at {format_local(anchor.post.created_at)}"
    )
    typer.echo(f"    {' '.join(anchor.post.text.split())}")
    typer.echo(f"Grade: {result.grade.level}")
    for reason in result.grade.reasons:
        typer.echo(f"  - {reason}")
    typer.echo(
        f"Flags: reversal: {'yes' if result.reversal else 'no'};"
        f" contradiction newer than the anchor: {'yes' if result.newer_contradiction else 'no'}"
    )
    typer.echo(_retrieval_line(result))
    _echo_group("Supporting", result.supporting)
    _echo_group("Contradicting", result.contradicting)
    _echo_group("Related", result.related)


def _choose_player(deps: CorroborationCliDeps, engine: Engine, text: str) -> PlayerRef:
    with Session(engine) as session:
        found = resolve_player(session, text, deps.aliases)
    if not found:
        raise fail(f"no player matches {text!r}")
    if len(found) > 1:
        lines = [f"  {p.fpl_id}  {p.web_name} ({p.team_name or '-'})" for p in found]
        raise fail(f"{text!r} is ambiguous, pass the FPL ID:\n" + "\n".join(lines))
    return found[0]


@app.command()
def corroborate_command(
    ctx: typer.Context,
    player: Annotated[str, typer.Argument(help="FPL ID or name.")],
    at: Annotated[str | None, typer.Option("--at", help="Europe/Warsaw time.")] = None,
    since: Annotated[str | None, typer.Option("--since", help="Europe/Warsaw time.")] = None,
    new_since: Annotated[
        str | None, typer.Option("--new-since", help="Europe/Warsaw time.")
    ] = None,
) -> None:
    deps = get_deps(ctx)
    if deps.engine is None:
        raise fail("DATABASE_URL must be set")
    as_of = _parse(at, "--at") or deps.clock.now()
    window_since = _parse(since, "--since")
    new_since_at = _parse(new_since, "--new-since")
    if window_since is not None and window_since >= as_of:
        raise fail("--since must be earlier than --at")
    chosen = _choose_player(deps, deps.engine, player)
    try:
        runtime = deps.make_runtime()
    except ConfigError as exc:
        raise fail(str(exc)) from None
    try:
        result = corroborate(
            deps.engine,
            chosen,
            as_of,
            new_since_at,
            since=window_since,
            runtime=runtime,
        )
    finally:
        runtime.tracer.flush()
    _echo_result(result)
    if result.trace_id:
        typer.echo(f"trace: {result.trace_id}")


def main() -> None:
    app(prog_name="python -m app.corroboration")
