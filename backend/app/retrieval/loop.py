import logging
import threading

from sqlalchemy import Engine
from sqlmodel import Session

from app.core.clock import Clock, StopAwareClock
from app.retrieval.embedder import Embedder
from app.retrieval.indexing import IndexingRuntime, embed_post
from app.retrieval.store import next_unembedded
from app.retrieval.tracing import make_tracer
from app.worker.jobs import Shutdown

logger = logging.getLogger(__name__)

IDLE_POLL_SECONDS = 2.0
ITERATION_ERROR_SLEEP_SECONDS = 30.0


class IndexingLoop:
    def __init__(
        self,
        engine: Engine,
        runtime: IndexingRuntime,
        clock: Clock,
        stop_event: threading.Event,
    ) -> None:
        self._engine = engine
        self._runtime = runtime
        self._clock = clock
        self._stop_event = stop_event

    def run(self) -> None:
        tracer = make_tracer(self._runtime.tracing)
        embedder: Embedder | None = None
        # Posts stored before the loop started are a backfill: their latency is not a
        # measure of the indexing path, so it is not recorded.
        started_at = self._clock.now()
        try:
            while not self._stop_event.is_set():
                try:
                    if embedder is None:
                        embedder = self._runtime.make_embedder()
                    with Session(self._engine) as session:
                        post = next_unembedded(session, self._runtime.model, self._clock.now())
                    if post is None:
                        self._clock.sleep(IDLE_POLL_SECONDS)
                        continue
                    embed_post(
                        self._engine,
                        self._runtime,
                        embedder,
                        post,
                        tracer,
                        self._clock,
                        self._stop_event,
                        record_latency=post.first_fetched_at >= started_at,
                    )
                except Exception as exc:
                    logger.error("indexing loop iteration failed: %s", type(exc).__name__)
                    self._clock.sleep(ITERATION_ERROR_SLEEP_SECONDS)
        finally:
            try:
                tracer.flush()
            except Exception as exc:
                logger.error("retrieval tracing flush failed: %s", type(exc).__name__)


def start_indexer(
    engine: Engine,
    runtime: IndexingRuntime,
    stop_event: threading.Event,
    clock: Clock | None = None,
) -> threading.Thread:
    loop = IndexingLoop(engine, runtime, clock or StopAwareClock(stop_event), stop_event)

    def target() -> None:
        try:
            loop.run()
        except Shutdown:
            pass

    thread = threading.Thread(target=target, name="indexer", daemon=True)
    thread.start()
    return thread
