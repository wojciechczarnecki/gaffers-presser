import asyncio
import json
import os
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from twscrape import API, NoAccountError

from app.core.errors import CollectorError
from app.tweets.sources.base import (
    SourcePayloadError,
    SourceRateLimitedError,
    SourceUnavailableError,
)
from app.tweets.sources.paging import collect_new
from app.tweets.sources.twscrape_source import TwscrapeSource
from tests.tweets.payloads import load

SENTINEL_COOKIES = "auth_token=sentinel-secret-auth; ct0=sentinel-secret-ct0"


class FakePool:
    def __init__(self, next_available=None):
        self.add_account_cookies_calls: list[tuple[str, str]] = []
        self._next_available = next_available

    async def add_account_cookies(self, username: str, cookies: str) -> None:
        self.add_account_cookies_calls.append((username, cookies))

    async def next_available_at(self, queue: str):
        return self._next_available


class FakeApi:
    def __init__(self, pages: list, pool: FakePool | None = None) -> None:
        self.pool = pool or FakePool()
        self._pages = pages
        self.closed = False

    async def list_timeline_raw(self, list_id: int):
        try:
            for page in self._pages:
                if isinstance(page, Exception):
                    raise page
                yield page
        finally:
            self.closed = True


def _response(name: str) -> httpx.Response:
    return httpx.Response(200, json=load(name))


def _source(api: FakeApi) -> TwscrapeSource:
    return TwscrapeSource(
        username="dedicated", cookies=SENTINEL_COOKIES, accounts_db=":unused:", api=api
    )


def test_page_normalised():
    api = FakeApi([_response("twscrape-page-1")])
    source = _source(api)
    try:
        page = next(source.pages(1))
    finally:
        source.close()

    assert [p.x_id for p in page] == [3003, 3002, 3001]
    repost, reply, normal = page
    assert (repost.is_repost, repost.is_reply) == (True, False)
    assert (reply.is_repost, reply.is_reply) == (False, True)
    assert normal.x_id == 3001
    assert normal.author_handle == "synthetic_leaker_3"
    assert normal.text == "Synthetic post text for testing purposes."
    assert normal.created_at == datetime(2026, 8, 6, 21, 6, 52, tzinfo=UTC)
    assert normal.created_at.utcoffset() == timedelta(0)
    assert (normal.is_repost, normal.is_reply) == (False, False)
    assert normal.raw["id_str"] == "3001"
    assert normal.raw["user"]["username"] == "synthetic_leaker_3"
    assert api.pool.add_account_cookies_calls == [("dedicated", SENTINEL_COOKIES)]


def test_stops_at_last_seen():
    api = FakeApi([_response("twscrape-page-1"), _response("twscrape-page-2")])
    source = _source(api)
    try:
        result = collect_new(source, list_id=1, since_id=2998)
    finally:
        source.close()

    assert [p.x_id for p in result] == [2997, 2998, 2999, 3001, 3002, 3003]
    assert api.closed is True


@pytest.mark.parametrize(
    "next_available,expected_error,expected_retry_after",
    [
        ("now", SourceRateLimitedError, 0.0),
        (None, SourceUnavailableError, None),
    ],
)
def test_no_account_error_maps_to_outcome(next_available, expected_error, expected_retry_after):
    api = FakeApi([NoAccountError("locked")], pool=FakePool(next_available=next_available))
    source = _source(api)
    try:
        with pytest.raises(expected_error) as exc_info:
            next(source.pages(1))
    finally:
        source.close()
    if expected_error is SourceRateLimitedError:
        assert exc_info.value.retry_after == expected_retry_after


def test_no_account_error_with_local_time_string(monkeypatch):
    class FixedDatetime:
        @staticmethod
        def now():
            import datetime as real_datetime

            return real_datetime.datetime(2026, 9, 28, 10, 0, 0)

        @staticmethod
        def strptime(value, fmt):
            import datetime as real_datetime

            return real_datetime.datetime.strptime(value, fmt)

    monkeypatch.setattr("app.tweets.sources.twscrape_source.datetime", FixedDatetime)

    api = FakeApi([NoAccountError("locked")], pool=FakePool(next_available="10:00:30"))
    source = _source(api)
    try:
        with pytest.raises(SourceRateLimitedError) as exc_info:
            next(source.pages(1))
    finally:
        source.close()
    assert exc_info.value.retry_after == pytest.approx(30.0)


def test_no_account_error_with_local_time_past_midnight(monkeypatch):
    class FixedDatetime:
        @staticmethod
        def now():
            import datetime as real_datetime

            return real_datetime.datetime(2026, 9, 28, 23, 59, 50)

        @staticmethod
        def strptime(value, fmt):
            import datetime as real_datetime

            return real_datetime.datetime.strptime(value, fmt)

    monkeypatch.setattr("app.tweets.sources.twscrape_source.datetime", FixedDatetime)

    api = FakeApi([NoAccountError("locked")], pool=FakePool(next_available="00:00:10"))
    source = _source(api)
    try:
        with pytest.raises(SourceRateLimitedError) as exc_info:
            next(source.pages(1))
    finally:
        source.close()
    assert exc_info.value.retry_after == pytest.approx(20.0)


def test_no_account_error_with_a_just_passed_time_is_now(monkeypatch):
    class FixedDatetime:
        @staticmethod
        def now():
            import datetime as real_datetime

            return real_datetime.datetime(2026, 9, 28, 10, 0, 0, 400_000)

        @staticmethod
        def strptime(value, fmt):
            import datetime as real_datetime

            return real_datetime.datetime.strptime(value, fmt)

    monkeypatch.setattr("app.tweets.sources.twscrape_source.datetime", FixedDatetime)

    api = FakeApi([NoAccountError("locked")], pool=FakePool(next_available="10:00:00"))
    source = _source(api)
    try:
        with pytest.raises(SourceRateLimitedError) as exc_info:
            next(source.pages(1))
    finally:
        source.close()
    assert exc_info.value.retry_after == 0.0


def test_no_account_error_with_garbage_next_available_falls_back_to_none():
    api = FakeApi([NoAccountError("locked")], pool=FakePool(next_available="garbage"))
    source = _source(api)
    try:
        with pytest.raises(SourceRateLimitedError) as exc_info:
            next(source.pages(1))
    finally:
        source.close()
    assert exc_info.value.retry_after is None


def test_malformed_page_raises_source_payload_error():
    api = FakeApi([httpx.Response(200, text="not json")])
    source = _source(api)
    try:
        with pytest.raises(SourcePayloadError):
            next(source.pages(1))
    finally:
        source.close()


def test_tws_telemetry_disabled_after_import():
    import app.tweets.sources.twscrape_source  # noqa: F401

    assert os.environ.get("TWS_TELEMETRY") == "0"


def test_empty_page_is_an_empty_list():
    payload = load("twscrape-page-1")
    payload["data"]["list"]["tweets_timeline"]["timeline"]["instructions"] = []
    api = FakeApi([httpx.Response(200, json=payload)])
    source = _source(api)
    try:
        assert next(source.pages(1)) == []
    finally:
        source.close()


def test_normalised_posts_carry_no_cookie():
    api = FakeApi([_response("twscrape-page-1")])
    source = _source(api)
    try:
        page = next(source.pages(1))
    finally:
        source.close()
    assert page
    for post in page:
        assert "sentinel-secret" not in json.dumps(post.raw)


@pytest.mark.parametrize(
    "script,pool",
    [
        (httpx.Response(200, text="not json"), FakePool()),
        (NoAccountError("locked"), FakePool(next_available="now")),
        (NoAccountError("locked"), FakePool(next_available=None)),
    ],
    ids=["malformed", "rate-limited", "no-account"],
)
def test_error_paths_never_carry_the_cookies(caplog, capfd, script, pool):
    source = _source(FakeApi([script], pool=pool))
    try:
        with caplog.at_level("DEBUG"):
            with pytest.raises(CollectorError) as exc:
                next(source.pages(1))
    finally:
        source.close()
    captured = capfd.readouterr()
    for text in (str(exc.value), repr(exc.value), caplog.text, captured.err, captured.out):
        assert "sentinel-secret" not in text


def test_real_twscrape_pool_rate_limit_carries_no_cookie(tmp_path, caplog, capfd):
    accounts_db = str(tmp_path / "accounts.db")

    async def lock_account() -> None:
        pool = API(pool=accounts_db).pool
        await pool.add_account_cookies("dedicated", SENTINEL_COOKIES)
        unlock_at = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())
        await pool.lock_until("dedicated", "ListLatestTweetsTimeline", unlock_at)

    asyncio.run(lock_account())
    source = TwscrapeSource(username="dedicated", cookies=SENTINEL_COOKIES, accounts_db=accounts_db)
    try:
        with caplog.at_level("DEBUG"):
            with pytest.raises(SourceRateLimitedError) as exc:
                next(source.pages(1))
    finally:
        source.close()

    assert 240 < exc.value.retry_after <= 300
    captured = capfd.readouterr()
    for text in (str(exc.value), repr(exc.value), caplog.text, captured.err, captured.out):
        assert "sentinel-secret" not in text
