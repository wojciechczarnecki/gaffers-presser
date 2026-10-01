import httpx
from sqlmodel import Session, select

from app.tweets.models import Tweet
from app.tweets.sources.twitterapi_io import TwitterApiIoSource
from app.tweets.sources.twscrape_source import TwscrapeSource
from app.tweets.sources.x_api import XApiSource
from app.tweets.store import store_posts
from tests.tweets.fakes import FakeHttp, post
from tests.tweets.payloads import load
from tests.tweets.sources.test_twscrape_source import FakeApi
from tests.tweets.sources.test_x_api import _PAGE1_QUERY


def _twscrape_page():
    api = FakeApi([httpx.Response(200, json=load("twscrape-page-1"))])
    source = TwscrapeSource(username="dedicated", cookies="c", accounts_db=":unused:", api=api)
    try:
        return next(source.pages(1))
    finally:
        source.close()


def _twitterapi_page():
    fake = FakeHttp({"twitter/list/tweets?listId=1&cursor=": load("twitterapi_io-page-1")})
    source = TwitterApiIoSource(api_key="k", transport=fake.transport())
    try:
        return next(source.pages(1))
    finally:
        source.close()


def _x_api_page():
    fake = FakeHttp({f"2/lists/42/tweets?{_PAGE1_QUERY}": load("x_api-page-1")})
    source = XApiSource(bearer_token="t", transport=fake.transport())
    try:
        return next(source.pages(42))
    finally:
        source.close()


def test_twscrape_repost_gets_its_original_author():
    repost, reply, normal = _twscrape_page()
    assert repost.is_repost
    assert repost.reposted_author_handle == "synthetic_leaker_2"
    assert reply.reposted_author_handle is None
    assert normal.reposted_author_handle is None


def test_twitterapi_io_repost_gets_its_original_author():
    repost, reply, normal = _twitterapi_page()
    assert repost.is_repost
    assert repost.reposted_author_handle == "synthetic_leaker_9"
    assert reply.reposted_author_handle is None
    assert normal.reposted_author_handle is None


def test_x_api_repost_gets_its_original_author_and_keeps_it_in_raw():
    repost, reply, normal = _x_api_page()
    assert repost.is_repost
    assert repost.reposted_author_handle == "synthetic_leaker_9"
    assert repost.raw["retweeted_author"]["username"] == "synthetic_leaker_9"
    assert reply.reposted_author_handle is None
    assert normal.reposted_author_handle is None
    assert "retweeted_author" not in normal.raw


def test_x_api_repost_whose_original_is_not_in_the_includes_is_still_stored():
    payload = load("x_api-page-1")
    payload["includes"]["tweets"] = []
    fake = FakeHttp({f"2/lists/42/tweets?{_PAGE1_QUERY}": payload})
    source = XApiSource(bearer_token="t", transport=fake.transport())
    try:
        page = next(source.pages(42))
    finally:
        source.close()
    assert [p.x_id for p in page] == [2002, 2001, 2000]
    assert page[0].is_repost and page[0].reposted_author_handle is None
    assert "retweeted_author" not in page[0].raw


def test_store_posts_persists_the_original_author(db):
    from datetime import UTC, datetime

    fetched = datetime(2026, 9, 28, 12, tzinfo=UTC)
    repost = post(1, is_repost=True, reposted_author_handle="origin")
    original = post(2)
    with Session(db) as session, session.begin():
        store_posts(session, [repost, original], "fake", fetched)
    with Session(db) as session:
        rows = {t.x_id: t.reposted_author_handle for t in session.exec(select(Tweet)).all()}
    assert rows == {1: "origin", 2: None}
