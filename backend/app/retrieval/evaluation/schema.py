from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Engine, create_engine, text
from sqlmodel import SQLModel

from app.retrieval.evaluation.dataset import CorpusPost, EvaluationError
from app.retrieval.models import PostEmbedding
from app.tweets.classes import source_post_sql
from app.tweets.models import ListMembership, Tweet

EVAL_SCHEMA = "retrieval_eval"
CORPUS_SOURCE = "eval-corpus"
MISMATCH_SHOWN = 5


def make_eval_engine(engine: Engine) -> Engine:
    return create_engine(
        engine.url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"options": f"-c timezone=UTC -c search_path={EVAL_SCHEMA},public"},
    )


def load_eval_corpus(
    engine: Engine, corpus: Sequence[CorpusPost], list_handles: Sequence[str] | None = None
) -> Engine:
    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {EVAL_SCHEMA} CASCADE"))
        conn.execute(text(f"CREATE SCHEMA {EVAL_SCHEMA}"))
        SQLModel.metadata.create_all(
            conn.execution_options(schema_translate_map={None: EVAL_SCHEMA}),
            tables=[Tweet.__table__, PostEmbedding.__table__, ListMembership.__table__],
        )
    eval_engine = make_eval_engine(engine)
    rows = [
        {
            "x_id": post.x_id,
            "author_handle": post.author_handle,
            "text": post.text,
            "created_at": post.created_at,
            "first_fetched_at": post.created_at,
            "source": CORPUS_SOURCE,
            "is_repost": post.is_repost,
            "is_reply": post.is_reply,
            "quoted_x_id": post.quoted_x_id,
            "raw": {},
        }
        for post in corpus
    ]
    with eval_engine.begin() as conn:
        if rows:
            conn.execute(Tweet.__table__.insert(), rows)
        if list_handles is not None:
            conn.execute(
                ListMembership.__table__.insert(),
                [
                    {
                        "list_id": 0,
                        "source": CORPUS_SOURCE,
                        "fetched_at": datetime.now(UTC),
                        "handles": list(list_handles),
                    }
                ],
            )
    check_source_flags(eval_engine, corpus)
    return eval_engine


def check_source_flags(eval_engine: Engine, corpus: Sequence[CorpusPost]) -> None:
    if any(post.is_source is None for post in corpus):
        return
    with eval_engine.connect() as conn:
        sql_sources = set(
            conn.execute(text(f"SELECT t.x_id FROM tweet t WHERE {source_post_sql('t')}"))
            .scalars()
            .all()
        )
    flagged = {post.x_id for post in corpus if post.is_source}
    wrong = sorted(sql_sources ^ flagged)
    if wrong:
        shown = ", ".join(str(x_id) for x_id in wrong[:MISMATCH_SHOWN])
        raise EvaluationError(
            f"{len(wrong)} corpus posts have is_source flags that disagree with the source rule:"
            f" {shown}"
        )
