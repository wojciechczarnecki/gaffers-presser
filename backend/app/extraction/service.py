import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import RunnableConfig
from sqlalchemy import Engine
from sqlmodel import Session

from app.core.errors import ConfigError
from app.extraction.config import TracingConfig
from app.extraction.flow import PROMPT_VERSION, Flow, build_flow
from app.extraction.linking import (
    PlayerAlias,
    PlayerIndex,
    TeamAlias,
    load_aliases,
    load_players,
)
from app.extraction.pricing import Price, compute_cost, load_prices
from app.extraction.providers import ChatModelSpec
from app.extraction.schemas import FlowResult, PostInput
from app.extraction.store import ExtractionRecord, save_extraction
from app.extraction.tracing import run_config
from app.tweets.loop import Clock
from app.tweets.models import Tweet

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS: tuple[float, ...] = (2.0, 4.0)

Aliases = tuple[list[PlayerAlias], list[TeamAlias]]


@dataclass(frozen=True)
class ExtractionRuntime:
    provider: str
    model: str
    make_spec: Callable[[], ChatModelSpec]
    tracing: TracingConfig | None
    prices: dict[str, Price]
    aliases: Aliases
    clock: Clock | None = None
    fallback_model: str | None = None


@dataclass(frozen=True)
class StoredOutcome:
    extraction_id: int
    status: str  # extracted | failed
    events: int
    cost_usd: float | None


@dataclass(frozen=True)
class RetryOutcome:
    result: FlowResult | None
    attempts: int
    error: Exception | None
    stopped: bool = False


def run_with_retries(
    flow: Flow,
    post: PostInput,
    config: RunnableConfig,
    clock: Clock,
    stop_event: threading.Event,
) -> RetryOutcome:
    attempts = 0
    last_exc: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if stop_event.is_set():
            return RetryOutcome(None, attempts, last_exc, stopped=True)
        attempts = attempt
        try:
            result = flow.run(post, config=config)
            return RetryOutcome(result, attempts, None)
        except Exception as exc:
            last_exc = exc
            logger.warning("extraction attempt failed: %s", type(exc).__name__)
            if attempt >= MAX_ATTEMPTS:
                break
            if stop_event.is_set():
                return RetryOutcome(None, attempts, last_exc, stopped=True)
            clock.sleep(RETRY_BACKOFF_SECONDS[attempt - 1])
            if stop_event.is_set():
                return RetryOutcome(None, attempts, last_exc, stopped=True)
    return RetryOutcome(None, attempts, last_exc)


def load_reference_files() -> tuple[dict[str, Price], Aliases]:
    try:
        return load_prices(), load_aliases()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ConfigError(
            f"prices.toml or aliases.toml cannot be loaded: {type(exc).__name__}"
        ) from None


def extract_post(
    engine: Engine,
    runtime: ExtractionRuntime,
    post: PostInput,
    clock: Clock,
    stop_event: threading.Event,
    handler: BaseCallbackHandler | None,
    record_latency: bool,
) -> StoredOutcome | None:
    started_at = clock.now()
    attempts = 0
    try:
        with Session(engine) as session:
            players, teams = load_players(session)
        player_aliases, team_aliases = runtime.aliases
        index = PlayerIndex(players, teams, player_aliases, team_aliases)
        flow = build_flow(runtime.make_spec(), index)
        config = run_config(post.x_id, PROMPT_VERSION, runtime.provider, runtime.model, handler)

        retry = run_with_retries(flow, post, config, clock, stop_event)
        if retry.stopped:
            return None
        attempts = retry.attempts
        finished_at = clock.now()
        if retry.result is not None:
            return _store_extracted(
                engine,
                runtime,
                post,
                retry.result,
                started_at,
                finished_at,
                attempts,
                record_latency,
            )
        error = retry.error
    except Exception as exc:
        logger.error("extraction of a post failed outside the model call: %s", type(exc).__name__)
        error = exc
        finished_at = clock.now()

    error_class = type(error).__name__ if error is not None else "UnknownError"
    return _store_failed(
        engine, runtime, post, error_class, started_at, finished_at, attempts, record_latency
    )


def _latency(session: Session, x_id: int, finished_at: datetime) -> float | None:
    tweet = session.get(Tweet, x_id)
    if tweet is None:
        return None
    return (finished_at - tweet.first_fetched_at).total_seconds()


def _store_extracted(
    engine: Engine,
    runtime: ExtractionRuntime,
    post: PostInput,
    result: FlowResult,
    started_at: datetime,
    finished_at: datetime,
    attempts: int,
    record_latency: bool,
) -> StoredOutcome:
    model = result.answered_model or runtime.model
    cost_usd = compute_cost(
        model,
        result.usage.input_tokens,
        result.usage.output_tokens,
        runtime.prices,
    )
    with Session(engine) as session:
        record = ExtractionRecord(
            tweet_x_id=post.x_id,
            status="extracted",
            provider=runtime.provider,
            model=model,
            prompt_version=PROMPT_VERSION,
            started_at=started_at,
            finished_at=finished_at,
            attempts=attempts,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            cost_usd=cost_usd,
            latency_seconds=_latency(session, post.x_id, finished_at) if record_latency else None,
        )
        extraction_id = save_extraction(session, record, result.events)
    return StoredOutcome(
        extraction_id=extraction_id,
        status="extracted",
        events=len(result.events),
        cost_usd=cost_usd,
    )


def _store_failed(
    engine: Engine,
    runtime: ExtractionRuntime,
    post: PostInput,
    error_class: str,
    started_at: datetime,
    finished_at: datetime,
    attempts: int,
    record_latency: bool,
) -> StoredOutcome:
    with Session(engine) as session:
        record = ExtractionRecord(
            tweet_x_id=post.x_id,
            status="failed",
            provider=runtime.provider,
            model=runtime.model,
            prompt_version=PROMPT_VERSION,
            started_at=started_at,
            finished_at=finished_at,
            attempts=attempts,
            error_class=error_class,
            latency_seconds=_latency(session, post.x_id, finished_at) if record_latency else None,
        )
        extraction_id = save_extraction(session, record, [])
    return StoredOutcome(extraction_id=extraction_id, status="failed", events=0, cost_usd=None)
