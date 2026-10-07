import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import Engine, bindparam, select, text
from sqlmodel import Session

from app.core.errors import CollectorError
from app.llm.pricing import Price
from app.retrieval.embedder import Embedder
from app.retrieval.indexing import traced_embed
from app.retrieval.tracing import NULL_TRACER, RetrievalTracer
from app.tweets.classes import source_post_sql
from app.tweets.models import Tweet

logger = logging.getLogger(__name__)

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
    sources_only: bool = False


@dataclass(frozen=True)
class SearchResult:
    x_id: int
    author_handle: str
    created_at: datetime
    text: str
    score: float
    ranks: dict[str, int | None]
    is_repost: bool = False
    reposted_author_handle: str | None = None


@dataclass(frozen=True)
class SearchResponse:
    mode: str
    model: str | None
    results: list[SearchResult]
    failed_legs: tuple[str, ...] = field(default=())
    failure: str | None = None


def fuse(rankings: Mapping[str, Sequence[int]], k: int) -> list[tuple[int, float, dict[str, int]]]:
    scores: dict[int, float] = {}
    ranks: dict[int, dict[str, int]] = {}
    for leg, ordered in rankings.items():
        for rank, x_id in enumerate(ordered, start=1):
            scores[x_id] = scores.get(x_id, 0.0) + 1.0 / (k + rank)
            ranks.setdefault(x_id, {})[leg] = rank
    order = sorted(scores, key=lambda x_id: (-scores[x_id], min(ranks[x_id].values()), -x_id))
    return [(x_id, scores[x_id], ranks[x_id]) for x_id in order]


NO_FILTERS = SearchFilters()

# chr(39) is a quote and chr(92) a backslash: each lexeme becomes a quoted term, a prefix
# term from MIN_PREFIX_LENGTH characters on, so that "o" or "m" does not match half the corpus.
MIN_PREFIX_LENGTH = 3
_FULLTEXT_SQL = """
WITH q AS (
    SELECT to_tsquery(
        'simple'::regconfig,
        string_agg(
            chr(39)
            || replace(replace(lexeme, chr(92), chr(92) || chr(92)), chr(39), chr(39) || chr(39))
            || chr(39)
            || CASE WHEN char_length(lexeme) >= :min_prefix THEN ':*' ELSE '' END,
            ' | '
        )
    ) AS query
    FROM unnest(to_tsvector('english_unaccent'::regconfig, :query))
)
SELECT t.x_id
FROM tweet t, q
WHERE q.query IS NOT NULL AND t.search_vector @@ q.query {filters}
ORDER BY ts_rank_cd(t.search_vector, q.query) DESC, t.created_at DESC, t.x_id DESC
LIMIT :depth
"""

_VECTOR_SQL = """
SELECT t.x_id
FROM post_embedding e JOIN tweet t ON t.x_id = e.tweet_x_id
WHERE e.model = :model AND e.status = 'embedded' {filters}
ORDER BY e.embedding <=> CAST(:q AS vector), t.x_id DESC
LIMIT :depth
"""


def _filter_sql(filters: SearchFilters) -> tuple[str, dict[str, Any]]:
    clauses = []
    params: dict[str, Any] = {}
    if filters.since is not None:
        clauses.append("AND t.created_at >= :since")
        params["since"] = filters.since
    if filters.until is not None:
        clauses.append("AND t.created_at < :until")
        params["until"] = filters.until
    if filters.exclude_reposts:
        clauses.append("AND NOT t.is_repost")
    if filters.exclude_replies:
        clauses.append("AND NOT t.is_reply")
    if filters.sources_only:
        clauses.append(f"AND {source_post_sql('t')}")
    return " ".join(clauses), params


def _fulltext_leg(session: Session, query: str, filters: SearchFilters, depth: int) -> list[int]:
    clause, params = _filter_sql(filters)
    statement = text(_FULLTEXT_SQL.format(filters=clause))
    rows = session.execute(
        statement, {"query": query, "depth": depth, "min_prefix": MIN_PREFIX_LENGTH, **params}
    )
    return [row[0] for row in rows]


def _vector_leg(
    session: Session, model: str, vector: list[float], filters: SearchFilters, depth: int
) -> list[int]:
    clause, params = _filter_sql(filters)
    statement = text(_VECTOR_SQL.format(filters=clause)).bindparams(bindparam("q", type_=VECTOR()))
    rows = session.execute(statement, {"model": model, "q": vector, "depth": depth, **params})
    return [row[0] for row in rows]


def _has_embeddings(session: Session, model: str) -> bool:
    return (
        session.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM post_embedding"
                " WHERE model = :model AND status = 'embedded')"
            ),
            {"model": model},
        ).scalar_one()
        is True
    )


def _filters_for_trace(filters: SearchFilters) -> dict[str, Any]:
    return {
        "since": filters.since.isoformat() if filters.since else None,
        "until": filters.until.isoformat() if filters.until else None,
        "exclude_reposts": filters.exclude_reposts,
        "exclude_replies": filters.exclude_replies,
        "sources_only": filters.sources_only,
    }


def search(
    engine: Engine,
    query: str,
    mode: Mode = "hybrid",
    *,
    embedder: Embedder | None = None,
    filters: SearchFilters = NO_FILTERS,
    limit: int = 10,
    k: int = 60,
    depth: int = 50,
    tracer: RetrievalTracer = NULL_TRACER,
    prices: dict[str, Price] | None = None,
    embed_timeout_seconds: float | None = None,
) -> SearchResponse:
    if mode not in ("fulltext", "vector", "hybrid"):
        raise SearchError(f"unknown search mode: {mode}")
    use_vector = mode in ("vector", "hybrid")
    if use_vector and embedder is None:
        raise SearchError("an embedder is needed for vector and hybrid search")
    model = embedder.model if use_vector and embedder is not None else None

    rankings: dict[str, list[int]] = {}
    failed_legs: list[str] = []
    failure: str | None = None
    vector: list[float] | None = None
    if use_vector and embedder is not None and model is not None:
        with Session(engine) as session:
            has_embeddings = _has_embeddings(session, model)
        if not has_embeddings:
            failure = (
                f"no embeddings for model {model}; "
                f"run python -m app.retrieval index --model {model}"
            )
            if mode == "vector":
                raise NoEmbeddingsError(failure)
        else:
            # Embedded before any session opens: the HTTP call holds no pooled connection.
            try:
                embedded, _ = traced_embed(
                    embedder, [query], tracer, prices or {}, embed_timeout_seconds
                )
            except Exception as exc:
                failure = f"query embedding failed: {type(exc).__name__}"
                if mode == "vector":
                    raise SearchError(failure) from None
            else:
                vector = embedded.vectors[0]
        if failure is not None:
            logger.error("vector leg failed: %s", failure)
            failed_legs.append("vector")

    with Session(engine) as session:
        if mode in ("fulltext", "hybrid"):
            rankings["fulltext"] = _fulltext_leg(session, query, filters, depth)
        if vector is not None and model is not None:
            rankings["vector"] = _vector_leg(session, model, vector, filters, depth)

        fused = fuse(rankings, k)[:limit]
        rows = {}
        if fused:
            tweets = session.execute(
                select(Tweet).where(Tweet.x_id.in_([x_id for x_id, _, _ in fused]))
            ).scalars()
            rows = {tweet.x_id: tweet for tweet in tweets}

    results = [
        SearchResult(
            x_id=x_id,
            author_handle=rows[x_id].author_handle,
            created_at=rows[x_id].created_at,
            text=rows[x_id].text,
            score=score,
            ranks={leg: legs.get(leg) for leg in LEGS},
            is_repost=rows[x_id].is_repost,
            reposted_author_handle=rows[x_id].reposted_author_handle,
        )
        for x_id, score, legs in fused
    ]
    ids_by_mode = dict(rankings)
    if mode == "hybrid":
        ids_by_mode["hybrid"] = [result.x_id for result in results]
    tracer.search(
        query=query,
        mode=mode,
        model=model,
        filters=_filters_for_trace(filters),
        ids_by_mode=ids_by_mode,
        failed_legs=tuple(failed_legs),
    )
    return SearchResponse(
        mode=mode,
        model=model,
        results=results,
        failed_legs=tuple(failed_legs),
        failure=failure,
    )
