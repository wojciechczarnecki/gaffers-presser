from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import httpx
from openrouter import OpenRouter
from openrouter.operations import CreateEmbeddingsResponseBody
from openrouter.utils import BackoffStrategy, RetryConfig
from pydantic import SecretStr

from app.retrieval.config import EmbeddingConfig

_REQUEST_TIMEOUT_MILLISECONDS = 30_000
_NO_RETRIES = RetryConfig("none", BackoffStrategy(0, 0, 1.0, 0), False)


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]
    input_tokens: int | None


class Embedder(Protocol):
    model: str

    def embed(
        self, texts: Sequence[str], *, timeout_seconds: float | None = None
    ) -> EmbeddingResult: ...


class OpenRouterEmbedder:
    def __init__(self, api_key: SecretStr, model: str, client: httpx.Client | None = None) -> None:
        self.model = model
        self._client = OpenRouter(
            api_key=api_key.get_secret_value(),
            client=client,
            timeout_ms=_REQUEST_TIMEOUT_MILLISECONDS,
        )
        # Without this the SDK retries 5XX on its own for up to an hour; the service owns the
        # attempts instead.
        self._client.sdk_configuration.retry_config = _NO_RETRIES

    def embed(
        self, texts: Sequence[str], *, timeout_seconds: float | None = None
    ) -> EmbeddingResult:
        options = {} if timeout_seconds is None else {"timeout_ms": round(timeout_seconds * 1000)}
        response = self._client.embeddings.generate(
            input=list(texts), model=self.model, encoding_format="float", **options
        )
        if not isinstance(response, CreateEmbeddingsResponseBody):
            raise ValueError("unexpected embeddings response")
        ordered = sorted(
            enumerate(response.data),
            key=lambda pair: (pair[1].index is None, pair[1].index, pair[0]),
        )
        vectors = []
        for _, item in ordered:
            if not isinstance(item.embedding, list):
                raise ValueError("embedding is not a list of floats")
            vectors.append([float(value) for value in item.embedding])
        if len(vectors) != len(texts):
            raise ValueError("embedding count does not match the input count")
        tokens = response.usage.prompt_tokens if response.usage else None
        return EmbeddingResult(vectors=vectors, input_tokens=tokens)


def build_embedder(config: EmbeddingConfig) -> Embedder:
    return OpenRouterEmbedder(config.api_key, config.model)
