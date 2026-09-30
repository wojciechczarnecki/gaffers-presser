import threading
from dataclasses import dataclass, field
from typing import Any, TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openrouter import ChatOpenRouter
from openrouter.utils import BackoffStrategy, RetryConfig
from pydantic import BaseModel, SecretStr

from app.core.clock import Clock
from app.llm.pricing import Price, compute_cost
from app.llm.retry import with_retries

# The ADR 0006 default chat model (a test keeps this equal to the extraction default); its
# request settings are those of its row in the extraction model settings.
DEFAULT_LABEL_MODEL = "openai/gpt-6-luna"
STRUCTURED_METHOD = "function_calling"
_REQUEST_TIMEOUT_MILLISECONDS = 60_000
_NO_RETRIES = RetryConfig("none", BackoffStrategy(0, 0, 1.0, 0), False)

T = TypeVar("T", bound=BaseModel)


class WrittenQuery(BaseModel):
    query: str


class RelevanceLabel(BaseModel):
    relevant: bool


def build_label_model(api_key: SecretStr, model: str) -> BaseChatModel:
    chat_model = ChatOpenRouter(
        model=model,
        api_key=api_key,
        timeout=_REQUEST_TIMEOUT_MILLISECONDS,
        max_retries=0,
        reasoning={"effort": "none"},
    )
    # With `max_retries=0` the SDK falls back to its own 5XX backoff for up to an hour.
    chat_model.client.sdk_configuration.retry_config = _NO_RETRIES
    return chat_model


def usage_cost(message: Any, model: str, prices: dict[str, Price]) -> float | None:
    usage = getattr(message, "usage_metadata", None) or {}
    return compute_cost(model, usage.get("input_tokens"), usage.get("output_tokens"), prices)


@dataclass
class StructuredCaller:
    chat_model: BaseChatModel
    model: str
    prices: dict[str, Price]
    clock: Clock
    stop_event: threading.Event = field(default_factory=threading.Event)
    cost_usd: float | None = None
    failures: int = 0

    def call(self, schema: type[T], system: str, human: str) -> T:
        def once() -> tuple[T, float | None]:
            structured = self.chat_model.with_structured_output(
                schema, include_raw=True, method=STRUCTURED_METHOD
            )
            result = structured.invoke([SystemMessage(system), HumanMessage(human)])
            if result["parsing_error"] is not None or result["parsed"] is None:
                raise ValueError("model output failed validation")
            return result["parsed"], usage_cost(result["raw"], self.model, self.prices)

        outcome = with_retries(once, self.clock, self.stop_event)
        if outcome.result is None:
            self.failures += 1
            raise outcome.error or RuntimeError("stopped")
        parsed, cost = outcome.result  # type: ignore[misc]
        if cost is not None:
            self.cost_usd = (self.cost_usd or 0.0) + cost
        return parsed
