from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from app.core.errors import CollectorError

Mode = Literal["fulltext", "vector", "hybrid"]
LEGS = ("fulltext", "vector")


class SearchError(CollectorError):
    pass


class NoEmbeddingsError(SearchError):
    pass


@dataclass(frozen=True)
class SearchFilters:
    since: datetime | None = None
    until: datetime | None = None
    exclude_reposts: bool = False
    exclude_replies: bool = False


@dataclass(frozen=True)
class SearchResult:
    x_id: int
    author_handle: str
    created_at: datetime
    text: str
    score: float
    ranks: dict[str, int | None]


@dataclass(frozen=True)
class SearchResponse:
    mode: str
    model: str | None
    results: list[SearchResult]
    failed_legs: tuple[str, ...] = field(default=())


def fuse(rankings: Mapping[str, Sequence[int]], k: int) -> list[tuple[int, float, dict[str, int]]]:
    scores: dict[int, float] = {}
    ranks: dict[int, dict[str, int]] = {}
    for leg, ordered in rankings.items():
        for rank, x_id in enumerate(ordered, start=1):
            scores[x_id] = scores.get(x_id, 0.0) + 1.0 / (k + rank)
            ranks.setdefault(x_id, {})[leg] = rank
    order = sorted(scores, key=lambda x_id: (-scores[x_id], min(ranks[x_id].values()), -x_id))
    return [(x_id, scores[x_id], ranks[x_id]) for x_id in order]
