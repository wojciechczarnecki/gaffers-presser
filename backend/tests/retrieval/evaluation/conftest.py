import pytest
from sqlalchemy import text

from app.retrieval.evaluation.schema import EVAL_SCHEMA


@pytest.fixture
def eval_db(db):
    yield db
    with db.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {EVAL_SCHEMA} CASCADE"))
