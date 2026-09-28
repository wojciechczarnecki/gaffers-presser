from datetime import UTC, datetime

import httpx
import pytest

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
    "&expansions=author_id&user.fields=username"
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
    assert repost.is_repost is True
    assert reply.is_reply is True
    assert normal.author_handle == "synthetic_leaker_1"
    assert normal.created_at == datetime(2026, 9, 28, 8, 0, 0, tzinfo=UTC)
    assert normal.raw["author"]["username"] == "synthetic_leaker_1"


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

    assert [p.x_id for p in result] == [1999, 2000, 2001, 2002]
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
        (httpx.Response(200, json={"data": [{"id": "1"}]}), SourcePayloadError),
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


def test_errors_never_carry_the_token(caplog):
    fake = FakeHttp({f"2/lists/42/tweets?{_PAGE1_QUERY}": httpx.Response(500)})
    source = _source(fake)
    try:
        with pytest.raises(SourceUnavailableError) as exc_info:
            with caplog.at_level("DEBUG"):
                next(source.pages(42))
    finally:
        source.close()
    assert SENTINEL_TOKEN not in str(exc_info.value)
    assert SENTINEL_TOKEN not in caplog.text
    assert fake.requests[0].headers["authorization"] == f"Bearer {SENTINEL_TOKEN}"
