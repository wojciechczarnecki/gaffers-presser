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
from app.tweets.sources.x_api import XApiSource
from tests.tweets.fakes import FakeHttp
from tests.tweets.payloads import load

SENTINEL_TOKEN = "sentinel-secret-token"  # noqa: S105


def _source(fake: FakeHttp, **kwargs) -> XApiSource:
    return XApiSource(bearer_token=SENTINEL_TOKEN, transport=fake.transport(), **kwargs)


_PAGE1_QUERY = (
    "max_results=20&tweet.fields=created_at%2Cauthor_id%2Creferenced_tweets"
    "&expansions=author_id%2Creferenced_tweets.id.author_id&user.fields=username"
)
_PAGE2_QUERY = _PAGE1_QUERY + "&pagination_token=token-2"


def test_page_normalised():
    fake = FakeHttp({f"2/lists/42/tweets?{_PAGE1_QUERY}": load("x_api-page-1")})
    source = _source(fake)
    try:
        page = next(source.pages(42))
    finally:
        source.close()

    assert [p.x_id for p in page] == [2002, 2001, 2000]
    repost, reply, normal = page
    assert (repost.is_repost, repost.is_reply) == (True, False)
    assert (reply.is_repost, reply.is_reply) == (False, True)
    assert reply.author_handle == "synthetic_leaker_2"
    assert normal.x_id == 2000
    assert normal.author_handle == "synthetic_leaker_1"
    assert normal.text == "Synthetic squad news for gameweek"
    assert normal.created_at == datetime(2026, 9, 28, 8, 0, 0, tzinfo=UTC)
    assert normal.created_at.utcoffset() == timedelta(0)
    assert (normal.is_repost, normal.is_reply) == (False, False)
    assert normal.raw["tweet"]["id"] == "2000"
    assert normal.raw["author"]["username"] == "synthetic_leaker_1"


def test_post_that_fails_to_map_is_skipped(caplog):
    payload = load("x_api-page-1")
    payload["data"][1]["author_id"] = "unknown-user"
    fake = FakeHttp({f"2/lists/42/tweets?{_PAGE1_QUERY}": payload})
    source = _source(fake)
    try:
        with caplog.at_level("WARNING"):
            page = next(source.pages(42))
    finally:
        source.close()

    assert [p.x_id for p in page] == [2002, 2000]
    assert "x_api: skipped a post that failed to map: KeyError" in caplog.text


def test_empty_page_without_errors_is_an_empty_page():
    fake = FakeHttp({f"2/lists/42/tweets?{_PAGE1_QUERY}": {"meta": {"result_count": 0}}})
    source = _source(fake)
    try:
        assert list(source.pages(42)) == [[]]
    finally:
        source.close()


def test_follows_token_until_last_seen():
    fake = FakeHttp(
        {
            f"2/lists/42/tweets?{_PAGE1_QUERY}": load("x_api-page-1"),
            f"2/lists/42/tweets?{_PAGE2_QUERY}": load("x_api-page-2"),
        }
    )
    source = _source(fake)
    try:
        result = collect_new(source, list_id=42, since_id=1998)
    finally:
        source.close()

    assert [p.x_id for p in result] == [1997, 1998, 1999, 2000, 2001, 2002]
    assert len(fake.requests) == 2


def test_rate_limit_uses_reset_header_against_injected_now():
    fake = FakeHttp(
        {
            f"2/lists/42/tweets?{_PAGE1_QUERY}": httpx.Response(
                429, headers={"x-rate-limit-reset": "1000"}
            )
        }
    )
    source = _source(fake, now=lambda: 970.0)
    try:
        with pytest.raises(SourceRateLimitedError) as exc_info:
            next(source.pages(42))
    finally:
        source.close()
    assert exc_info.value.retry_after == 30.0


@pytest.mark.parametrize(
    "response,expected_error",
    [
        (httpx.Response(500), SourceUnavailableError),
        (httpx.Response(401), SourceUnavailableError),
        (httpx.Response(200, text="not json"), SourcePayloadError),
        (httpx.Response(200, json={"data": "not-a-list"}), SourcePayloadError),
        (
            httpx.Response(200, json={"errors": [{"title": "Not Found Error"}]}),
            SourceUnavailableError,
        ),
    ],
)
def test_error_paths(response, expected_error):
    fake = FakeHttp({f"2/lists/42/tweets?{_PAGE1_QUERY}": response})
    source = _source(fake)
    try:
        with pytest.raises(expected_error):
            next(source.pages(42))
    finally:
        source.close()


def test_rate_limit_without_reset_header_has_none():
    fake = FakeHttp({f"2/lists/42/tweets?{_PAGE1_QUERY}": httpx.Response(429)})
    source = _source(fake)
    try:
        with pytest.raises(SourceRateLimitedError) as exc_info:
            next(source.pages(42))
    finally:
        source.close()
    assert exc_info.value.retry_after is None


def test_rate_limit_with_past_reset_is_zero():
    fake = FakeHttp(
        {
            f"2/lists/42/tweets?{_PAGE1_QUERY}": httpx.Response(
                429, headers={"x-rate-limit-reset": "900"}
            )
        }
    )
    source = _source(fake, now=lambda: 970.0)
    try:
        with pytest.raises(SourceRateLimitedError) as exc_info:
            next(source.pages(42))
    finally:
        source.close()
    assert exc_info.value.retry_after == 0.0


def test_connect_error_is_source_unavailable():
    def raise_connect_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    fake = FakeHttp({f"2/lists/42/tweets?{_PAGE1_QUERY}": raise_connect_error})
    source = _source(fake)
    try:
        with pytest.raises(SourceUnavailableError):
            next(source.pages(42))
    finally:
        source.close()


def _raise_connect_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError(f"boom {request.headers['authorization']}")


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, headers={"x-rate-limit-reset": "1000"}),
        httpx.Response(500),
        httpx.Response(401),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"data": "not-a-list"}),
        httpx.Response(200, json={"errors": [{"title": "Not Found Error"}]}),
        _raise_connect_error,
    ],
    ids=["429", "500", "401", "not-json", "bad-data", "errors", "connect-error"],
)
def test_error_paths_never_carry_the_token(caplog, response):
    fake = FakeHttp({f"2/lists/42/tweets?{_PAGE1_QUERY}": response})
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
    assert fake.requests[0].headers["authorization"] == f"Bearer {SENTINEL_TOKEN}"


def test_normalised_posts_carry_no_token():
    fake = FakeHttp({f"2/lists/42/tweets?{_PAGE1_QUERY}": load("x_api-page-1")})
    source = _source(fake)
    try:
        page = next(source.pages(42))
    finally:
        source.close()
    assert page
    for post in page:
        assert "sentinel-secret" not in json.dumps(post.raw)
