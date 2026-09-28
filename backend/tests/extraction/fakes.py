import threading
import uuid
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult, LLMResult
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
    """

    responses: list[Any]

    _index: int = PrivateAttr(default=0)
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
        message = AIMessage(
            content="",
            tool_calls=[
                {"name": tool_name, "args": args, "id": f"fake-{uuid.uuid4()}"},
            ],
            usage_metadata={
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
            },
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


class RecordingHandler(BaseCallbackHandler):
    def __init__(self) -> None:
        self.chat_model_starts: list[dict[str, Any]] = []
        self.llm_ends: list[dict[str, Any]] = []
        self._parents: dict[UUID, UUID | None] = {}

    def on_chain_start(
        self,
        serialized: dict[str, Any],
        inputs: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        self._parents[run_id] = parent_run_id

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self._parents[run_id] = parent_run_id
        self.chat_model_starts.append(
            {
                "run_id": run_id,
                "parent_run_id": parent_run_id,
                "metadata": metadata or {},
                "messages": messages,
            }
        )

    def root_run_id(self, run_id: UUID) -> UUID:
        seen: set[UUID] = set()
        while run_id in self._parents and self._parents[run_id] is not None:
            parent = self._parents[run_id]
            assert parent is not None
            if parent in seen:
                break
            seen.add(parent)
            run_id = parent
        return run_id

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        usage = None
        for generations in response.generations:
            for generation in generations:
                message = getattr(generation, "message", None)
                usage_metadata = getattr(message, "usage_metadata", None)
                if usage_metadata:
                    usage = usage_metadata
        self.llm_ends.append(
            {
                "run_id": run_id,
                "parent_run_id": parent_run_id,
                "usage": usage,
            }
        )
