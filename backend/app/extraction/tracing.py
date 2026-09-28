from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import RunnableConfig
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler

from app.extraction.config import TracingConfig


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


def run_config(
    x_id: int,
    prompt_version: str,
    provider: str,
    model: str,
    handler: BaseCallbackHandler | None,
    run_name: str | None = None,
) -> RunnableConfig:
    metadata: dict[str, Any] = {
        "x_id": x_id,
        "prompt_version": prompt_version,
        "provider": provider,
        "model": model,
    }
    if run_name is not None:
        metadata["langfuse_session_id"] = run_name
        metadata["langfuse_tags"] = [run_name]
    config: RunnableConfig = {"run_name": "extraction", "metadata": metadata}
    if handler is not None:
        config["callbacks"] = [handler]
    return config


def flush(handler: CallbackHandler | None) -> None:
    if handler is None:
        return
    handler._langfuse_client.flush()
