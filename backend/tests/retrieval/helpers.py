from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine
from sqlmodel import Session

from app.llm.pricing import Price
from app.retrieval.embedder import Embedder
from app.retrieval.indexing import IndexingRuntime
from app.tweets.models import Tweet

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
MODEL = "fake/embed"
PRICES = {MODEL: Price(input_per_million=0.02, output_per_million=None, checked="x")}


class FixedClock:
    def __init__(self, now: datetime = NOW) -> None:
        self.current = now
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def add_tweet(
    engine: Engine,
    x_id: int,
    text: str = "Saka is fit again",
    created_at: datetime | None = None,
    first_fetched_at: datetime | None = None,
    is_repost: bool = False,
    is_reply: bool = False,
    author: str = "reporter",
    reposted_author_handle: str | None = None,
) -> None:
    created_at = created_at or NOW - timedelta(hours=1)
    with Session(engine) as session:
        session.add(
            Tweet(
                x_id=x_id,
                author_handle=author,
                text=text,
                created_at=created_at,
                first_fetched_at=first_fetched_at or created_at,
                source="list",
                is_repost=is_repost,
                is_reply=is_reply,
                reposted_author_handle=reposted_author_handle,
                raw={},
            )
        )
        session.commit()


def runtime(embedder: Embedder, model: str = MODEL, clock=None) -> IndexingRuntime:
    return IndexingRuntime(
        model=model,
        make_embedder=lambda: embedder,
        prices=PRICES,
        tracing=None,
        clock=clock,
    )


class FastClock:
    def __init__(self, stop_event, start: datetime = NOW) -> None:
        self._stop_event = stop_event
        self.current = start
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += timedelta(seconds=seconds)
        self._stop_event.wait(0.01)
