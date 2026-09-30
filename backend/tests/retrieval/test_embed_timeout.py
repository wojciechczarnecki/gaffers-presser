import json
from pathlib import Path

import httpx
from pydantic import SecretStr
from sqlmodel import Session

from app.retrieval.embedder import OpenRouterEmbedder
from app.retrieval.search import search
from app.retrieval.store import save_embedded
from tests.retrieval.fakes import FakeEmbedder, SlowEmbedder
from tests.retrieval.helpers import MODEL, NOW, PRICES, add_tweet

PAYLOAD = json.loads(
    (Path(__file__).parent / "payloads" / "openrouter_embeddings.json").read_text()
)


def _timeouts(**call) -> dict:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=PAYLOAD)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    OpenRouterEmbedder(SecretStr("dummy-key"), MODEL, client=client).embed(
        ["first", "second"], **call
    )
    return seen[0].extensions["timeout"]


def test_a_per_call_timeout_reaches_the_request():
    assert _timeouts(timeout_seconds=2.5)["read"] == 2.5


def test_the_default_timeout_stays_thirty_seconds():
    assert _timeouts()["read"] == 30.0


def _seed(db) -> None:
    add_tweet(db, 1, "Saka injury doubt")
    add_tweet(db, 2, "Saka scores again")
    with Session(db) as session, session.begin():
        save_embedded(
            session,
            x_id=1,
            model=MODEL,
            vector=[1.0, 0.0, 0.0],
            input_tokens=5,
            cost_usd=None,
            latency_seconds=None,
            attempts=1,
            now=NOW,
        )


def test_a_slow_embedding_falls_back_to_fulltext(db):
    _seed(db)
    embedder = SlowEmbedder(delay_seconds=3.0)
    response = search(
        db,
        "saka injury",
        "hybrid",
        embedder=embedder,
        prices=PRICES,
        embed_timeout_seconds=1.0,
    )
    assert response.failed_legs == ("vector",)
    assert response.failure is not None and "TimeoutError" in response.failure
    assert [r.x_id for r in response.results][0] == 1
    assert embedder.timeouts == [1.0]


def test_a_generous_timeout_lets_the_slow_embedding_through(db):
    _seed(db)
    response = search(
        db,
        "saka injury",
        "hybrid",
        embedder=SlowEmbedder(delay_seconds=3.0),
        prices=PRICES,
        embed_timeout_seconds=10.0,
    )
    assert response.failed_legs == ()


def test_search_without_a_timeout_calls_embed_with_no_keyword(db):
    _seed(db)
    calls: list[dict] = []

    class Recording(FakeEmbedder):
        def embed(self, texts, **kwargs):
            calls.append(kwargs)
            return super().embed(texts)

    search(db, "saka injury", "hybrid", embedder=Recording(), prices=PRICES)
    assert calls == [{}]
