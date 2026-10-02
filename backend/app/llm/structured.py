import threading
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.core.clock import Clock
from app.core.retry import with_retries
from app.llm.chat import ChatModelSpec
from app.llm.pricing import Price, compute_cost

T = TypeVar("T", bound=BaseModel)


def _add_optional[N: (int, float)](a: N | None, b: N | None) -> N | None:
    if a is None and b is None:
        return None
    return (a or 0) + (b or 0)


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    reported_cost_usd: float | None = None

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=_add_optional(self.input_tokens, other.input_tokens),
            output_tokens=_add_optional(self.output_tokens, other.output_tokens),
            reasoning_tokens=_add_optional(self.reasoning_tokens, other.reasoning_tokens),
            reported_cost_usd=_add_optional(self.reported_cost_usd, other.reported_cost_usd),
        )


def usage_from_raw(raw: Any) -> Usage:
    usage_metadata = getattr(raw, "usage_metadata", None) or {}
    response_metadata = getattr(raw, "response_metadata", None) or {}
    output_details = usage_metadata.get("output_token_details") or {}
    return Usage(
        input_tokens=usage_metadata.get("input_tokens"),
        output_tokens=usage_metadata.get("output_tokens"),
        reasoning_tokens=output_details.get("reasoning"),
        reported_cost_usd=response_metadata.get("cost"),
    )


def answer_from_raw(raw: Any) -> tuple[str | None, str | None, str | None]:
    response_metadata = getattr(raw, "response_metadata", None) or {}
    # The pinned SDK's ChatResult drops `provider`, so the host comes from the generation lookup;
    # the read stays so a client that keeps the field skips that lookup (runner._with_host).
    return (
        response_metadata.get("model_name"),
        response_metadata.get("provider"),
        response_metadata.get("id"),
    )


@dataclass(frozen=True)
class StructuredReply(Generic[T]):
    parsed: T
    usage: Usage
    cost_usd: float | None
    answered_model: str | None


@dataclass
class StructuredCaller:
    chat_model: BaseChatModel
    model: str
    prices: dict[str, Price]
    clock: Clock
    stop_event: threading.Event = field(default_factory=threading.Event)
    cost_usd: float | None = None
    failures: int = 0
    structured_kwargs: dict[str, Any] = field(
        default_factory=lambda: {"method": "function_calling"}
    )

    @classmethod
    def from_spec(
        cls, spec: ChatModelSpec, prices: dict[str, Price], clock: Clock
    ) -> "StructuredCaller":
        return cls(
            spec.chat_model,
            spec.model,
            prices,
            clock,
            structured_kwargs=dict(spec.structured_kwargs),
        )

    def call(self, schema: type[T], system: str, human: str) -> T:
        return self.call_with_usage(schema, system, human).parsed

    def call_with_usage(self, schema: type[T], system: str, human: str) -> StructuredReply[T]:
        def once() -> StructuredReply[T]:
            structured = self.chat_model.with_structured_output(
                schema, include_raw=True, **self.structured_kwargs
            )
            result = structured.invoke([SystemMessage(system), HumanMessage(human)])
            if result["parsing_error"] is not None or result["parsed"] is None:
                raise ValueError("model output failed validation")
            usage = usage_from_raw(result["raw"])
            answered_model, _, _ = answer_from_raw(result["raw"])
            priced_model = answered_model or self.model
            cost = compute_cost(priced_model, usage.input_tokens, usage.output_tokens, self.prices)
            return StructuredReply(result["parsed"], usage, cost, answered_model)

        outcome = with_retries(once, self.clock, self.stop_event, what="chat")
        if outcome.result is None:
            self.failures += 1
            raise outcome.error or RuntimeError("stopped")
        if outcome.result.cost_usd is not None:
            self.cost_usd = (self.cost_usd or 0.0) + outcome.result.cost_usd
        return outcome.result
