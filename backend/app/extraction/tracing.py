from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import RunnableConfig


def run_config(
    x_id: int | str,
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
