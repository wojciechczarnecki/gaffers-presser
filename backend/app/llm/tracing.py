from dataclasses import dataclass

from langfuse import Langfuse
from langfuse.langchain import CallbackHandler
from pydantic import SecretStr

from app.llm.settings import LlmSettings


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
