from collections.abc import Sequence

from sqlalchemy import Engine, create_engine, text
from sqlmodel import SQLModel

from app.retrieval.evaluation.dataset import CorpusPost
from app.retrieval.models import PostEmbedding
from app.tweets.models import Tweet

EVAL_SCHEMA = "retrieval_eval"
CORPUS_SOURCE = "eval-corpus"


def make_eval_engine(engine: Engine) -> Engine:
    return create_engine(
        engine.url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"options": f"-c timezone=UTC -c search_path={EVAL_SCHEMA},public"},
    )


def load_eval_corpus(engine: Engine, corpus: Sequence[CorpusPost]) -> Engine:
    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {EVAL_SCHEMA} CASCADE"))
        conn.execute(text(f"CREATE SCHEMA {EVAL_SCHEMA}"))
        SQLModel.metadata.create_all(
            conn.execution_options(schema_translate_map={None: EVAL_SCHEMA}),
            tables=[Tweet.__table__, PostEmbedding.__table__],
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
            "raw": {},
        }
        for post in corpus
    ]
    if rows:
        with eval_engine.begin() as conn:
            conn.execute(Tweet.__table__.insert(), rows)
    return eval_engine
