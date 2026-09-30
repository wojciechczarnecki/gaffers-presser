import asyncio
import json
import logging
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

os.environ.setdefault("TWS_TELEMETRY", "0")

import twscrape  # noqa: E402
from twscrape import API, NoAccountError  # noqa: E402

from app.tweets.sources.base import (
    FetchedPost,
    SourcePayloadError,
    SourceRateLimitedError,
    SourceUnavailableError,
)

twscrape.set_log_level("ERROR")

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

_QUEUE = "ListLatestTweetsTimeline"
_PAST_TOLERANCE = timedelta(hours=12)


def _retry_after_from_next_available(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    if value == "now":
        return 0.0
    try:
        target_time = datetime.strptime(value, "%H:%M:%S").time()
    except ValueError:
        return None
    now_local = datetime.now()
    target_dt = now_local.replace(
        hour=target_time.hour,
        minute=target_time.minute,
        second=target_time.second,
        microsecond=0,
    )
    if target_dt <= now_local:
        # twscrape truncates to whole seconds, so a lock ending within the second reads
        # as already past; only a time far in the past means the lock ends tomorrow.
        if now_local - target_dt <= _PAST_TOLERANCE:
            return 0.0
        target_dt += timedelta(days=1)
    return (target_dt - now_local).total_seconds()


def _to_post(tweet) -> FetchedPost:
    return FetchedPost(
        x_id=tweet.id,
        author_handle=tweet.user.username,
        text=tweet.rawContent,
        created_at=tweet.date.astimezone(UTC),
        is_repost=tweet.retweetedTweet is not None,
        is_reply=tweet.inReplyToTweetId is not None,
        raw=json.loads(tweet.json()),
        reposted_author_handle=(
            tweet.retweetedTweet.user.username if tweet.retweetedTweet is not None else None
        ),
    )


class TwscrapeSource:
    name = "twscrape"
    max_pages = 5

    def __init__(
        self,
        username: str,
        cookies: str,
        accounts_db: str,
        api=None,
    ) -> None:
        self._runner = asyncio.Runner()
        try:
            self._api = (
                api if api is not None else API(pool=accounts_db, raise_when_no_account=True)
            )
            self._runner.run(self._api.pool.add_account_cookies(username, cookies))
        except Exception:
            self._runner.close()
            raise

    def close(self) -> None:
        self._runner.close()

    def pages(self, list_id: int) -> Iterator[list[FetchedPost]]:
        gen = self._api.list_timeline_raw(list_id)
        try:
            while True:
                try:
                    response = self._runner.run(anext(gen))
                except StopAsyncIteration:
                    return
                except NoAccountError:
                    next_available = self._runner.run(self._api.pool.next_available_at(_QUEUE))
                    if next_available is None:
                        raise SourceUnavailableError("twscrape: no active account") from None
                    raise SourceRateLimitedError(
                        "twscrape: rate limited",
                        _retry_after_from_next_available(next_available),
                    ) from None

                try:
                    page_dict = response.json()
                    page = [_to_post(tweet) for tweet in twscrape.parse_tweets(page_dict)]
                except Exception as exc:
                    raise SourcePayloadError("twscrape: malformed page") from exc

                yield page
        finally:
            self._runner.run(gen.aclose())
