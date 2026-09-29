import logging
from collections.abc import Sequence
from typing import Any, Protocol

from langfuse import Langfuse

from app.llm.tracing import TracingConfig, make_client

logger = logging.getLogger(__name__)


class RetrievalTracer(Protocol):
    def embedding(
        self,
        *,
        model: str,
        texts: Sequence[str],
        input_tokens: int | None,
        cost_usd: float | None,
    ) -> None: ...

    def search(
        self,
        *,
        query: str,
        mode: str,
        model: str | None,
        filters: dict[str, Any],
        ids_by_mode: dict[str, list[int]],
        failed_legs: Sequence[str],
    ) -> None: ...

    def flush(self) -> None: ...


class NullTracer:
    def embedding(self, **_: Any) -> None:
        return None

    def search(self, **_: Any) -> None:
        return None

    def flush(self) -> None:
        return None


NULL_TRACER = NullTracer()


class LangfuseTracer:
    def __init__(self, client: Langfuse) -> None:
        self._client = client

    def embedding(
        self,
        *,
        model: str,
        texts: Sequence[str],
        input_tokens: int | None,
        cost_usd: float | None,
    ) -> None:
        usage = {"input": input_tokens} if input_tokens is not None else None
        cost = {"input": cost_usd} if cost_usd is not None else None
        self._client.start_observation(
            name="embedding",
            as_type="embedding",
            model=model,
            input=list(texts),
            usage_details=usage,
            cost_details=cost,
        ).end()

    def search(
        self,
        *,
        query: str,
        mode: str,
        model: str | None,
        filters: dict[str, Any],
        ids_by_mode: dict[str, list[int]],
        failed_legs: Sequence[str],
    ) -> None:
        self._client.start_observation(
            name="retrieval-search",
            as_type="retriever",
            input={"query": query, "mode": mode, "filters": filters},
            output={leg: [str(x_id) for x_id in ids] for leg, ids in ids_by_mode.items()},
            metadata={"model": model, "failed_legs": list(failed_legs)},
        ).end()

    def flush(self) -> None:
        self._client.flush()


def make_tracer(tracing: TracingConfig | None) -> RetrievalTracer:
    if tracing is None:
        logger.warning(
            "retrieval tracing disabled: LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set"
        )
        return NULL_TRACER
    try:
        return LangfuseTracer(make_client(tracing))
    except Exception as exc:
        logger.error("retrieval tracing could not start: %s", type(exc).__name__)
        return NULL_TRACER
