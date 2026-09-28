import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.core.errors import CollectorError
from app.tweets.sources.base import (
    SourcePayloadError,
    SourceRateLimitedError,
    SourceUnavailableError,
)
from app.tweets.sources.paging import collect_new
from app.tweets.sources.twitterapi_io import TwitterApiIoSource
from tests.tweets.fakes import FakeHttp
from tests.tweets.payloads import load

SENTINEL_KEY = "sentinel-secret-key"


def _source(fake: FakeHttp) -> TwitterApiIoSource:
    return TwitterApiIoSource(api_key=SENTINEL_KEY, transport=fake.transport())


def test_page_normalised():
    fake = FakeHttp(
        {
            "twitter/list/tweets?listId=42&cursor=": load("twitterapi_io-page-1"),
        }
    )
    source = _source(fake)
    try:
        page = next(source.pages(42))
    finally:
        source.close()

    assert [p.x_id for p in page] == [1002, 1001, 1000]
    repost, reply, normal = page
    assert (repost.is_repost, repost.is_reply) == (True, False)
    assert (reply.is_repost, reply.is_reply) == (False, True)
    assert reply.author_handle == "synthetic_leaker_2"
    assert normal.x_id == 1000
    assert (normal.is_repost, normal.is_reply) == (False, False)
    assert normal.created_at.utcoffset() == timedelta(0)
    assert normal.author_handle == "synthetic_leaker_1"
    assert normal.text == "Synthetic squad news for gameweek"
    assert normal.created_at == datetime(2026, 9, 28, 8, 0, 0, tzinfo=UTC)
    assert normal.raw["id"] == "1000"


def test_post_that_fails_to_map_is_skipped(caplog):
    payload = load("twitterapi_io-page-1")
    payload["tweets"][1]["author"] = None
    fake = FakeHttp({"twitter/list/tweets?listId=42&cursor=": payload})
    source = _source(fake)
    try:
        with caplog.at_level("WARNING"):
            page = next(source.pages(42))
    finally:
        source.close()

    assert [p.x_id for p in page] == [1002, 1000]
    assert "twitterapi_io: skipped a post that failed to map: TypeError" in caplog.text


def test_follows_cursor_until_last_seen():
    fake = FakeHttp(
        {
            "twitter/list/tweets?listId=42&cursor=": load("twitterapi_io-page-1"),
            "twitter/list/tweets?listId=42&cursor=cursor-2": load("twitterapi_io-page-2"),
        }
    )
    source = _source(fake)
    try:
        result = collect_new(source, list_id=42, since_id=998)
    finally:
        source.close()

    assert [p.x_id for p in result] == [997, 998, 999, 1000, 1001, 1002]
    assert len(fake.requests) == 2


@pytest.mark.parametrize(
    "response,expected_error",
    [
        (httpx.Response(429), SourceRateLimitedError),
        (httpx.Response(500), SourceUnavailableError),
        (httpx.Response(401), SourceUnavailableError),
        (httpx.Response(200, json={"status": "error", "tweets": []}), SourceUnavailableError),
        (httpx.Response(200, text="not json"), SourcePayloadError),
        (httpx.Response(200, json={"status": "success"}), SourcePayloadError),
        (httpx.Response(200, json=["not", "an", "object"]), SourcePayloadError),
    ],
)
def test_error_paths(response, expected_error):
    fake = FakeHttp({"twitter/list/tweets?listId=42&cursor=": response})
    source = _source(fake)
    try:
        with pytest.raises(expected_error):
            next(source.pages(42))
    finally:
        source.close()


def test_rate_limit_without_retry_after_has_none():
    fake = FakeHttp({"twitter/list/tweets?listId=42&cursor=": httpx.Response(429)})
    source = _source(fake)
    try:
        with pytest.raises(SourceRateLimitedError) as exc_info:
            next(source.pages(42))
    finally:
        source.close()
    assert exc_info.value.retry_after is None


def test_rate_limit_with_retry_after():
    fake = FakeHttp(
        {
            "twitter/list/tweets?listId=42&cursor=": httpx.Response(
                429, headers={"Retry-After": "30"}
            )
        }
    )
    source = _source(fake)
    try:
        with pytest.raises(SourceRateLimitedError) as exc_info:
            next(source.pages(42))
    finally:
        source.close()
    assert exc_info.value.retry_after == 30.0


def test_connect_error_is_source_unavailable():
    def raise_connect_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    fake = FakeHttp({"twitter/list/tweets?listId=42&cursor=": raise_connect_error})
    source = _source(fake)
    try:
        with pytest.raises(SourceUnavailableError):
            next(source.pages(42))
    finally:
        source.close()


def test_empty_page_is_an_empty_list():
    fake = FakeHttp(
        {
            "twitter/list/tweets?listId=42&cursor=": {
                "status": "success",
                "tweets": [],
                "has_next_page": False,
                "next_cursor": "",
            }
        }
    )
    source = _source(fake)
    try:
        assert list(source.pages(42)) == [[]]
    finally:
        source.close()


def _raise_connect_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError(f"boom {request.headers['x-api-key']}")


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, headers={"Retry-After": "30"}),
        httpx.Response(500),
        httpx.Response(401),
        httpx.Response(200, json={"status": "error", "msg": "bad", "tweets": []}),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"status": "success"}),
        _raise_connect_error,
    ],
    ids=["429", "500", "401", "status-error", "not-json", "no-tweets", "connect-error"],
)
def test_error_paths_never_carry_the_key(caplog, response):
    fake = FakeHttp({"twitter/list/tweets?listId=42&cursor=": response})
    source = _source(fake)
    try:
        with caplog.at_level("DEBUG"):
            with pytest.raises(CollectorError) as exc_info:
                next(source.pages(42))
    finally:
        source.close()
    for text in (str(exc_info.value), repr(exc_info.value), caplog.text):
        assert "sentinel-secret" not in text
    assert exc_info.value.__cause__ is None
    assert fake.requests[0].headers["x-api-key"] == SENTINEL_KEY
    assert "sentinel-secret" not in str(fake.requests[0].url)


def test_normalised_posts_carry_no_key():
    fake = FakeHttp({"twitter/list/tweets?listId=42&cursor=": load("twitterapi_io-page-1")})
    source = _source(fake)
    try:
        page = next(source.pages(42))
    finally:
        source.close()
    assert page
    for post in page:
        assert "sentinel-secret" not in json.dumps(post.raw)
