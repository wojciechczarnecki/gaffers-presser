import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import RunnableConfig
from sqlalchemy import Engine
from sqlmodel import Session

from app.extraction.config import TracingConfig
from app.extraction.flow import PROMPT_VERSION, Flow, build_flow
from app.extraction.linking import PlayerIndex, load_aliases, load_players
from app.extraction.pricing import compute_cost, load_prices
from app.extraction.providers import ChatModelSpec
from app.extraction.schemas import FlowResult, PostInput
from app.extraction.store import ExtractionRecord, save_extraction
from app.extraction.tracing import run_config
from app.tweets.loop import Clock
from app.tweets.models import Tweet

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS: tuple[float, ...] = (2.0, 4.0)


@dataclass(frozen=True)
class ExtractionRuntime:
    provider: str
    model: str
    make_spec: Callable[[], ChatModelSpec]
    tracing: TracingConfig | None
    clock: Clock | None = None


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

    with Session(engine) as session:
        players, teams = load_players(session)
    player_aliases, team_aliases = load_aliases()
    index = PlayerIndex(players, teams, player_aliases, team_aliases)

    spec = runtime.make_spec()
    flow = build_flow(spec, index)
    config = run_config(post.x_id, PROMPT_VERSION, runtime.provider, runtime.model, handler)

    retry = run_with_retries(flow, post, config, clock, stop_event)
    if retry.stopped:
        return None
    attempts = retry.attempts
    last_exc = retry.error
    result = retry.result

    finished_at = clock.now()

    with Session(engine) as session:
        latency_seconds = None
        if record_latency:
            tweet = session.get(Tweet, post.x_id)
            if tweet is not None:
                latency_seconds = (finished_at - tweet.first_fetched_at).total_seconds()

        if result is not None:
            prices = load_prices()
            cost_usd = compute_cost(
                runtime.provider,
                runtime.model,
                result.usage.input_tokens,
                result.usage.output_tokens,
                prices,
            )
            record = ExtractionRecord(
                tweet_x_id=post.x_id,
                status="extracted",
                provider=runtime.provider,
                model=runtime.model,
                prompt_version=PROMPT_VERSION,
                started_at=started_at,
                finished_at=finished_at,
                attempts=attempts,
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
                cost_usd=cost_usd,
                latency_seconds=latency_seconds,
            )
            extraction_id = save_extraction(session, record, result.events)
            return StoredOutcome(
                extraction_id=extraction_id,
                status="extracted",
                events=len(result.events),
                cost_usd=cost_usd,
            )

        error_class = type(last_exc).__name__ if last_exc is not None else "UnknownError"
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
            latency_seconds=latency_seconds,
        )
        extraction_id = save_extraction(session, record, [])
        return StoredOutcome(extraction_id=extraction_id, status="failed", events=0, cost_usd=None)
