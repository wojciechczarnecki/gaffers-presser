from datetime import UTC, datetime

from sqlalchemy import text
from sqlmodel import Session

from app.tweets.store import store_posts
from tests.tweets.fakes import post

NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)


def test_post_findable_by_fulltext_in_the_storing_transaction(db):
    with Session(db) as session:
        store_posts(session, [post(x_id=7, text="Saka picked up an injury")], "twscrape", NOW)
        found = (
            session.execute(
                text("SELECT x_id FROM tweet WHERE search_vector @@ to_tsquery('simple', 'injuri')")
            )
            .scalars()
            .all()
        )
        assert found == [7]
        session.rollback()
