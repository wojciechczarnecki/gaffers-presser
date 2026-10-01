import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC

import typer
from sqlalchemy import Engine

from app.core.clock import Clock, SystemClock
from app.core.errors import ConfigError
from app.core.settings import load_settings
from app.db.engine import make_engine
from app.delivery.channels import build_channel
from app.delivery.channels.base import Channel
from app.delivery.config import DeliveryConfig, DeliverySettings, resolve_delivery
from app.delivery.content import load_test_message
from app.delivery.service import DeliveryService
from app.delivery.store import recent_rows

TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
STATUS_ROWS = 10

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Message delivery: send a test message and inspect the delivery log.",
)


@app.callback()
def _callback() -> None:
    # Keeps `app` a multi-command group whatever the number of commands.
    pass


@dataclass(frozen=True)
class DeliveryCliDeps:
    engine: Engine | None
    config: DeliveryConfig | None
    make_channel: Callable[[], Channel]
    clock: Clock


def fail(message: str) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(1)


def db_engine(deps: DeliveryCliDeps) -> Engine:
    if deps.engine is None:
        raise fail("DATABASE_URL must be set")
    return deps.engine


def get_deps(ctx: typer.Context) -> DeliveryCliDeps:
    if ctx.obj is None:  # tests inject their own
        ctx.obj = _deps_from_settings()
    return ctx.obj


def _engine_from_env() -> Engine | None:
    try:
        return make_engine(load_settings().database_url)
    except ConfigError:
        return None  # commands that need no database run without it; the others say so


def _deps_from_settings() -> DeliveryCliDeps:
    clock = SystemClock()
    try:
        config = resolve_delivery(DeliverySettings())
    except ConfigError as exc:
        raise fail(str(exc)) from None

    def make_channel() -> Channel:
        assert config is not None
        return build_channel(config, clock)

    return DeliveryCliDeps(
        engine=_engine_from_env(), config=config, make_channel=make_channel, clock=clock
    )


@app.command(name="send-test", help="Send the test message through the configured channel.")
def send_test(ctx: typer.Context) -> None:
    deps = get_deps(ctx)
    if deps.config is None:
        raise fail("delivery is disabled (DELIVERY_PROVIDER is empty)")
    engine = db_engine(deps)
    now = deps.clock.now()
    key = f"test:{now.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}"
    channel = deps.make_channel()
    try:
        outcome = DeliveryService(engine, channel, deps.clock).send(
            key, "test", load_test_message(now)
        )
    finally:
        channel.close()
    typer.echo(f"status: {outcome.status}")
    typer.echo(f"provider id: {outcome.provider_message_id or '-'}")
    typer.echo(f"log id: {outcome.log_id}")
    if outcome.status != "sent":
        typer.echo(f"error class: {outcome.error_class or '-'}")
        raise typer.Exit(1)


@app.command(help="Show the configured channel and the last delivery log rows.")
def status(ctx: typer.Context) -> None:
    deps = get_deps(ctx)
    typer.echo(f"channel: {deps.config.provider if deps.config else 'disabled'}")
    rows = recent_rows(db_engine(deps), STATUS_ROWS)
    if not rows:
        typer.echo("no sends yet")
    for row in rows:
        when = row.requested_at.astimezone(UTC).strftime(TIME_FORMAT)
        typer.echo(
            f"{when}  {row.kind}  {row.status}  attempts={row.attempts}"
            f"  provider_id={row.provider_message_id or '-'}"
        )


def main() -> None:
    app(prog_name="python -m app.delivery")
