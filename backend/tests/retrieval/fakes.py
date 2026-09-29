import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.retrieval.embedder import EmbeddingResult

DIMENSIONS = 3


def hash_vector(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode()).digest()
    return [digest[i] / 255 + 0.01 for i in range(DIMENSIONS)]


class FakeEmbedder:
    def __init__(
        self,
        model: str = "fake/embed",
        vectors: dict[str, list[float]] | None = None,
        default: list[float] | None = None,
        responses: list[Exception | None] | None = None,
        tokens_per_text: int = 5,
    ) -> None:
        self.model = model
        self.vectors = vectors or {}
        self.default = default
        self.responses = list(responses or [])
        self.tokens_per_text = tokens_per_text
        self.calls: list[list[str]] = []

    def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        self.calls.append(list(texts))
        if self.responses:
            scripted = self.responses.pop(0)
            if scripted is not None:
                raise scripted
        vectors = []
        for text in texts:
            if text in self.vectors:
                vectors.append(list(self.vectors[text]))
            elif self.default is not None:
                vectors.append(list(self.default))
            else:
                vectors.append(hash_vector(text))
        return EmbeddingResult(vectors=vectors, input_tokens=self.tokens_per_text * len(texts))


class AlwaysFailingEmbedder(FakeEmbedder):
    def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        self.calls.append(list(texts))
        raise RuntimeError("embedding backend down")


@dataclass
class RecordingTracer:
    embeddings: list[dict[str, Any]] = field(default_factory=list)
    searches: list[dict[str, Any]] = field(default_factory=list)
    flushed: int = 0

    def embedding(self, **kwargs: Any) -> None:
        self.embeddings.append(kwargs)

    def search(self, **kwargs: Any) -> None:
        self.searches.append(kwargs)

    def flush(self) -> None:
        self.flushed += 1
