import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, ContextManager, Protocol

from langfuse import Langfuse
from langfuse.langchain import CallbackHandler
from pydantic import SecretStr

from app.llm.settings import LlmSettings
from app.llm.structured import Usage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TracingConfig:
    public_key: SecretStr
    secret_key: SecretStr
    host: str


def resolve_tracing(settings: LlmSettings) -> TracingConfig | None:
    if settings.langfuse_public_key and settings.langfuse_secret_key:
        return TracingConfig(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    return None


def make_handler(tracing: TracingConfig | None) -> CallbackHandler | None:
    if tracing is None:
        return None
    public_key = tracing.public_key.get_secret_value()
    Langfuse(
        public_key=public_key,
        secret_key=tracing.secret_key.get_secret_value(),
        host=tracing.host,
    )
    return CallbackHandler(public_key=public_key)


def flush(handler: CallbackHandler | None) -> None:
    if handler is None:
        return
    handler._langfuse_client.flush()


def make_client(tracing: TracingConfig) -> Langfuse:
    return Langfuse(
        public_key=tracing.public_key.get_secret_value(),
        secret_key=tracing.secret_key.get_secret_value(),
        host=tracing.host,
    )


class SpanHandle(Protocol):
    trace_id: str | None

    def update(self, *, output: Any) -> None: ...


class GenerationTracer(Protocol):
    def span(self, name: str, input: dict[str, Any]) -> ContextManager[SpanHandle]: ...

    def generation(
        self,
        *,
        model: str,
        input: str,
        output: str | None,
        usage: Usage | None,
        cost_usd: float | None,
        latency_seconds: float | None = None,
        error_class: str | None = None,
    ) -> None: ...

    def flush(self) -> None: ...


class _NullSpan:
    trace_id: str | None = None

    def update(self, *, output: Any) -> None:
        return None


NULL_SPAN = _NullSpan()


class NullGenerationTracer:
    @contextmanager
    def span(self, name: str, input: dict[str, Any]) -> Iterator[SpanHandle]:
        yield NULL_SPAN

    def generation(self, **_: Any) -> None:
        return None

    def flush(self) -> None:
        return None


class _LangfuseSpan:
    def __init__(self, observation: Any, label: str) -> None:
        self._observation = observation
        self._label = label
        self.trace_id: str | None = getattr(observation, "trace_id", None)

    def update(self, *, output: Any) -> None:
        try:
            self._observation.update(output=output)
        except Exception as exc:
            logger.error("%s tracing failed: %s", self._label, type(exc).__name__)


class LangfuseGenerationTracer:
    # A tracing failure never fails the traced work: it is logged by class name and dropped.
    def __init__(self, client: Langfuse, *, label: str, generation_name: str) -> None:
        self._client = client
        self._label = label
        self._generation_name = generation_name

    def _failed(self, exc: Exception, what: str = "tracing failed") -> None:
        logger.error("%s %s: %s", self._label, what, type(exc).__name__)

    @contextmanager
    def span(self, name: str, input: dict[str, Any]) -> Iterator[SpanHandle]:
        try:
            manager = self._client.start_as_current_observation(
                name=name, as_type="chain", input=input
            )
            observation = manager.__enter__()
        except Exception as exc:
            self._failed(exc)
            yield NULL_SPAN
            return
        try:
            yield _LangfuseSpan(observation, self._label)
        except BaseException as exc:
            self._close(manager, type(exc), exc, exc.__traceback__)
            raise
        self._close(manager, None, None, None)

    def _close(self, manager: Any, *exc_info: Any) -> None:
        try:
            manager.__exit__(*exc_info)
        except Exception as exc:
            self._failed(exc)

    def generation(
        self,
        *,
        model: str,
        input: str,
        output: str | None,
        usage: Usage | None,
        cost_usd: float | None,
        latency_seconds: float | None = None,
        error_class: str | None = None,
    ) -> None:
        usage_details = None
        if usage is not None and usage.input_tokens is not None:
            usage_details = {"input": usage.input_tokens, "output": usage.output_tokens or 0}
        extra: dict[str, Any] = {}
        if latency_seconds is not None:
            extra["metadata"] = {"latency_seconds": latency_seconds}
        try:
            self._client.start_observation(
                name=self._generation_name,
                as_type="generation",
                model=model,
                input=input,
                output=output,
                usage_details=usage_details,
                cost_details={"total": cost_usd} if cost_usd is not None else None,
                level="ERROR" if error_class is not None else None,
                status_message=error_class,
                **extra,
            ).end()
        except Exception as exc:
            self._failed(exc)

    def flush(self) -> None:
        try:
            self._client.flush()
        except Exception as exc:
            self._failed(exc, "tracing flush failed")


def make_generation_tracer[T](
    tracing: TracingConfig | None, label: str, build: Callable[[Langfuse], T], null: T
) -> T:
    if tracing is None:
        logger.warning(
            "%s tracing disabled: LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set", label
        )
        return null
    try:
        return build(make_client(tracing))
    except Exception as exc:
        logger.error("%s tracing could not start: %s", label, type(exc).__name__)
        return null
