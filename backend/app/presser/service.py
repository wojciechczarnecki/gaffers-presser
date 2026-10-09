import logging
import threading
import time
from dataclasses import dataclass, field

from sqlalchemy import Engine
from sqlmodel import Session, func, select

from app.core.clock import Clock, SystemClock
from app.core.errors import CollectorError
from app.delivery.service import DeliveryService
from app.fpl.models import Gameweek
from app.llm.structured import Usage
from app.presser.facts import FactSheet, NoFactsError, build_fact_sheet
from app.presser.render import render_presser
from app.presser.store import (
    insert_presser,
    key_has_status,
    mark_generated_sent,
    previous_pressers,
    sent_delivery_log_id,
    update_presser,
)
from app.presser.tracing import PresserTracer
from app.presser.writer import PROMPT_VERSION, Writer, WriterInput, human_message

logger = logging.getLogger(__name__)


class PresserFailed(CollectorError):  # noqa: N818
    pass


class PresserStopped(CollectorError):  # noqa: N818
    pass


PREVIEW_FAILED = "preview_failed"


def presser_key(season: str, gameweek: int, league_id: int) -> str:
    return f"presser:{season}:gw{gameweek}:league{league_id}"


@dataclass
class PresserRuntime:
    writer: Writer
    model: str
    tracer: PresserTracer
    nicknames: dict[int, str]
    delivery: DeliveryService | None
    league_ids: list[int]
    stop_event: threading.Event = field(default_factory=threading.Event)
    clock: Clock = field(default_factory=SystemClock)


@dataclass(frozen=True)
class GeneratedPresser:
    id: int
    text: str
    facts: FactSheet
    usage: Usage
    cost_usd: float | None
    latency_seconds: float


def league_ordinal(runtime: PresserRuntime, league_id: int) -> str:
    if league_id in runtime.league_ids:
        return f"#{runtime.league_ids.index(league_id) + 1}"
    return "#?"


def generate_presser(
    engine: Engine,
    runtime: PresserRuntime,
    season: str,
    league_id: int,
    gameweek: int,
    *,
    preview: bool = False,
) -> GeneratedPresser:
    key = presser_key(season, gameweek, league_id)
    with Session(engine) as session:
        facts = build_fact_sheet(session, season, league_id, gameweek, runtime.nicknames)
        previous = previous_pressers(session, season, league_id, gameweek)
    item = WriterInput(facts, previous)
    common = {
        "season": season,
        "league_fpl_id": league_id,
        "gameweek_fpl_id": gameweek,
        "idempotency_key": key,
        "facts": facts.model_dump(mode="json"),
        "model": runtime.model,
        "prompt_version": PROMPT_VERSION,
        "created_at": runtime.clock.now(),
    }
    human = human_message(item)
    started = time.monotonic()
    with runtime.tracer.span("presser", {"season": season, "gameweek": gameweek}) as span:
        try:
            reply = runtime.writer.run(item)
        except Exception as exc:
            latency = time.monotonic() - started
            if runtime.stop_event.is_set():
                raise PresserStopped("stopped while writing") from None
            error_class = type(exc).__name__
            runtime.tracer.generation(
                model=runtime.model,
                input=human,
                output=None,
                usage=None,
                cost_usd=None,
                latency_seconds=latency,
                error_class=error_class,
            )
            # A failed preview must not count as the worker's attempt for this key.
            insert_presser(
                engine,
                **common,
                status=PREVIEW_FAILED if preview else "failed",
                error_class=error_class,
                latency_seconds=latency,
                trace_id=span.trace_id,
            )
            logger.error(
                "presser generation failed: gameweek=%s league=%s error=%s",
                gameweek,
                league_ordinal(runtime, league_id),
                error_class,
            )
            raise PresserFailed(error_class) from None
        latency = time.monotonic() - started
        text = reply.parsed.text
        runtime.tracer.generation(
            model=runtime.model,
            input=human,
            output=text,
            usage=reply.usage,
            cost_usd=reply.cost_usd,
            latency_seconds=latency,
        )
        span.update(output={"characters": len(text)})
        trace_id = span.trace_id
    row_id = insert_presser(
        engine,
        **common,
        text=text,
        status="generated",
        input_tokens=reply.usage.input_tokens,
        output_tokens=reply.usage.output_tokens,
        cost_usd=reply.cost_usd,
        latency_seconds=latency,
        trace_id=trace_id,
    )
    runtime.tracer.flush()
    return GeneratedPresser(row_id, text, facts, reply.usage, reply.cost_usd, latency)


def send_presser(
    engine: Engine,
    runtime: PresserRuntime,
    season: str,
    league_id: int,
    gameweek: int,
    *,
    skip_statuses: tuple[str, ...],
) -> str:
    key = presser_key(season, gameweek, league_id)
    ordinal = league_ordinal(runtime, league_id)
    with Session(engine) as session:
        if key_has_status(session, key, skip_statuses):
            logger.info(
                "presser skipped: gameweek=%s league=%s already attempted", gameweek, ordinal
            )
            return "skipped"
        delivered_log_id = sent_delivery_log_id(session, key)
    if delivered_log_id is not None:
        # Delivered, but the process stopped before the row was marked: mark it, no new call.
        mark_generated_sent(engine, key, delivered_log_id)
        logger.info("presser already_sent: gameweek=%s league=%s", gameweek, ordinal)
        return "already_sent"
    if runtime.delivery is None:
        raise PresserFailed("DeliveryDisabled")
    generated = generate_presser(engine, runtime, season, league_id, gameweek)
    message = render_presser(generated.facts.league, gameweek, generated.text)
    outcome = runtime.delivery.send(key, "presser", message)
    if outcome.status == "sent":
        update_presser(engine, generated.id, status="sent", delivery_log_id=outcome.log_id)
        status = "sent"
    elif outcome.status == "already_sent":
        status = "already_sent"
    else:
        error_class = outcome.error_class or "DeliveryDisabled"
        update_presser(engine, generated.id, status="failed", error_class=error_class)
        status = "failed"
    logger.info("presser %s: gameweek=%s league=%s", status, gameweek, ordinal)
    return status


def latest_finished_gameweek(session: Session, season: str) -> int | None:
    return session.exec(
        select(func.max(Gameweek.fpl_id)).where(Gameweek.season == season, Gameweek.finished)
    ).one()


def run_after_league_sync(
    engine: Engine, runtime: PresserRuntime, season: str, gameweek: int
) -> None:
    with Session(engine) as session:
        latest = latest_finished_gameweek(session, season)
    if gameweek != latest:
        logger.info("presser skipped: gameweek=%s is not the latest finished", gameweek)
        return
    for league_id in runtime.league_ids:
        if runtime.stop_event.is_set():
            return
        try:
            send_presser(
                engine, runtime, season, league_id, gameweek, skip_statuses=("sent", "failed")
            )
        except PresserStopped:
            return
        except PresserFailed:
            continue
        except NoFactsError as exc:
            logger.error(
                "presser skipped: gameweek=%s league=%s error=%s",
                gameweek,
                league_ordinal(runtime, league_id),
                type(exc).__name__,
            )
        except Exception as exc:
            logger.error(
                "presser failed: gameweek=%s league=%s error=%s",
                gameweek,
                league_ordinal(runtime, league_id),
                type(exc).__name__,
            )
