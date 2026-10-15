import threading
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel as PydanticBaseModel
from pydantic import PrivateAttr


class FakeChatModel(BaseChatModel):
    """A chat model whose responses are scripted, for use with `with_structured_output`.

    Each entry of `responses` is consumed in order, once per call:
    - a Pydantic model instance or a dict → returned as a tool call named after the
      schema bound with `bind_tools`, carrying `usage_metadata`;
    - an `Exception` instance → raised;
    - a `threading.Event` → the call blocks on `.wait()` until it is set, then the next
      scripted entry is used for that same call.

    Entries are handed out under a lock, so concurrent calls never share one.
    """

    responses: list[Any]
    response_model: str | None = None
    host: str | None = None
    reasoning_tokens: int | None = None
    reported_cost: float | None = None
    generation_id: str | None = None

    _index: int = PrivateAttr(default=0)
    _lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)
    _bound_tool_name: str | None = PrivateAttr(default=None)
    _received_messages: list[list[BaseMessage]] = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "fake-chat-model"

    @property
    def received_messages(self) -> list[list[BaseMessage]]:
        return self._received_messages

    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Any:
        schema = tools[0]
        tool_name = schema.__name__ if isinstance(schema, type) else getattr(schema, "name", None)
        return self.bind(_fake_tool_name=tool_name)

    def _next_response(self) -> Any:
        while True:
            with self._lock:
                item = self.responses[self._index]
                self._index += 1
            if isinstance(item, threading.Event):
                item.wait()
                continue
            return item

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self._received_messages.append(messages)
        response = self._next_response()
        if isinstance(response, BaseException):
            raise response
        if isinstance(response, PydanticBaseModel):
            args = response.model_dump()
        else:
            args = dict(response)
        tool_name = kwargs.get("_fake_tool_name") or self._bound_tool_name
        usage_metadata: dict[str, Any] = {
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
        }
        if self.reasoning_tokens is not None:
            usage_metadata["output_token_details"] = {"reasoning": self.reasoning_tokens}
        response_metadata: dict[str, Any] = {}
        if self.host is not None:
            response_metadata["provider"] = self.host
        if self.reported_cost is not None:
            response_metadata["cost"] = self.reported_cost
        if self.generation_id is not None:
            response_metadata["id"] = self.generation_id
        message = AIMessage(
            content="",
            tool_calls=[
                {"name": tool_name, "args": args, "id": f"fake-{uuid.uuid4()}"},
            ],
            usage_metadata=usage_metadata,
            response_metadata=response_metadata,
        )
        llm_output = {"model_name": self.response_model} if self.response_model else None
        return ChatResult(generations=[ChatGeneration(message=message)], llm_output=llm_output)


class RecordingExtractionTracer:
    def __init__(self) -> None:
        self.spans: list[dict[str, Any]] = []
        self.generations: list[dict[str, Any]] = []
        self.flushed = 0

    @contextmanager
    def span(
        self, name: str, input: dict[str, Any], session_id: str | None = None
    ) -> Iterator[Any]:
        record: dict[str, Any] = {
            "name": name,
            "input": input,
            "session_id": session_id,
            "outputs": [],
            "generations": [],
        }
        self.spans.append(record)
        self._open = record

        class Handle:
            trace_id = "trace-rec"

            @staticmethod
            def update(*, output: Any) -> None:
                record["outputs"].append(output)

        try:
            yield Handle()
        finally:
            self._open = None

    def generation(self, **kwargs: Any) -> None:
        self.generations.append(kwargs)
        if getattr(self, "_open", None) is not None:
            self._open["generations"].append(kwargs)

    def flush(self) -> None:
        self.flushed += 1
