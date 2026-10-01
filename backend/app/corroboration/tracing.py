import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, ContextManager, Protocol

from langfuse import Langfuse

from app.llm.structured import Usage
from app.llm.tracing import TracingConfig, make_client
from app.retrieval.tracing import NULL_TRACER, LangfuseTracer, RetrievalTracer

logger = logging.getLogger(__name__)


class SpanHandle(Protocol):
    trace_id: str | None

    def update(self, *, output: Any) -> None: ...


class CorroborationTracer(Protocol):
    def span(self, name: str, input: dict[str, Any]) -> ContextManager[SpanHandle]: ...

    def generation(
        self,
        *,
        model: str,
        input: str,
        output: str | None,
        usage: Usage | None,
        cost_usd: float | None,
        error_class: str | None = None,
    ) -> None: ...

    def retrieval(self) -> RetrievalTracer: ...

    def flush(self) -> None: ...


class _NullSpan:
    trace_id: str | None = None

    def update(self, *, output: Any) -> None:
        return None


_NULL_SPAN = _NullSpan()


class NullCorroborationTracer:
    @contextmanager
    def span(self, name: str, input: dict[str, Any]) -> Iterator[SpanHandle]:
        yield _NULL_SPAN

    def generation(self, **_: Any) -> None:
        return None

    def retrieval(self) -> RetrievalTracer:
        return NULL_TRACER

    def flush(self) -> None:
        return None


NULL_CORROBORATION_TRACER = NullCorroborationTracer()


class _LangfuseSpan:
    def __init__(self, observation: Any) -> None:
        self._observation = observation
        self.trace_id: str | None = getattr(observation, "trace_id", None)

    def update(self, *, output: Any) -> None:
        try:
            self._observation.update(output=output)
        except Exception as exc:
            logger.error("corroboration tracing failed: %s", type(exc).__name__)


class LangfuseCorroborationTracer:
    # A tracing failure never fails the traced work: it is logged by class name and dropped.
    def __init__(self, client: Langfuse) -> None:
        self._client = client

    @contextmanager
    def span(self, name: str, input: dict[str, Any]) -> Iterator[SpanHandle]:
        try:
            manager = self._client.start_as_current_observation(
                name=name, as_type="chain", input=input
            )
            observation = manager.__enter__()
        except Exception as exc:
            logger.error("corroboration tracing failed: %s", type(exc).__name__)
            yield _NULL_SPAN
            return
        try:
            yield _LangfuseSpan(observation)
        except BaseException as exc:
            self._close(manager, type(exc), exc, exc.__traceback__)
            raise
        self._close(manager, None, None, None)

    @staticmethod
    def _close(manager: Any, *exc_info: Any) -> None:
        try:
            manager.__exit__(*exc_info)
        except Exception as exc:
            logger.error("corroboration tracing failed: %s", type(exc).__name__)

    def generation(
        self,
        *,
        model: str,
        input: str,
        output: str | None,
        usage: Usage | None,
        cost_usd: float | None,
        error_class: str | None = None,
    ) -> None:
        usage_details = None
        if usage is not None and usage.input_tokens is not None:
            usage_details = {"input": usage.input_tokens, "output": usage.output_tokens or 0}
        try:
            self._client.start_observation(
                name="corroboration-judge",
                as_type="generation",
                model=model,
                input=input,
                output=output,
                usage_details=usage_details,
                cost_details={"total": cost_usd} if cost_usd is not None else None,
                level="ERROR" if error_class is not None else None,
                status_message=error_class,
            ).end()
        except Exception as exc:
            logger.error("corroboration tracing failed: %s", type(exc).__name__)

    def retrieval(self) -> RetrievalTracer:
        return LangfuseTracer(self._client)

    def flush(self) -> None:
        try:
            self._client.flush()
        except Exception as exc:
            logger.error("corroboration tracing flush failed: %s", type(exc).__name__)


def make_corroboration_tracer(tracing: TracingConfig | None) -> CorroborationTracer:
    if tracing is None:
        logger.warning(
            "corroboration tracing disabled: LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set"
        )
        return NULL_CORROBORATION_TRACER
    try:
        return LangfuseCorroborationTracer(make_client(tracing))
    except Exception as exc:
        logger.error("corroboration tracing could not start: %s", type(exc).__name__)
        return NULL_CORROBORATION_TRACER
