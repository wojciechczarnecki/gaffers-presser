import logging
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx

from app.tweets.sources.base import (
    FetchedPost,
    SourcePayloadError,
    SourceRateLimitedError,
    SourceUnavailableError,
)

logger = logging.getLogger(__name__)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

_CREATED_AT_FORMAT = "%a %b %d %H:%M:%S %z %Y"


def _retry_after(response: httpx.Response) -> float | None:
    header = response.headers.get("Retry-After")
    if header is None:
        return None
    try:
        return float(header)
    except ValueError:
        return None


def _reposted_author(tweet: dict) -> str | None:
    retweeted = tweet.get("retweeted_tweet")
    if retweeted is None:
        return None
    return (retweeted.get("author") or {}).get("userName")


def _to_post(tweet: dict) -> FetchedPost:
    return FetchedPost(
        x_id=int(tweet["id"]),
        author_handle=tweet["author"]["userName"],
        text=tweet["text"],
        created_at=datetime.strptime(tweet["createdAt"], _CREATED_AT_FORMAT).astimezone(UTC),
        is_repost=tweet.get("retweeted_tweet") is not None,
        is_reply=bool(tweet.get("isReply")),
        raw=tweet,
        reposted_author_handle=_reposted_author(tweet),
    )


def _map_posts(tweets: list) -> list[FetchedPost]:
    page = []
    for tweet in tweets:
        try:
            page.append(_to_post(tweet))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            logger.warning(
                "twitterapi_io: skipped a post that failed to map: %s", type(exc).__name__
            )
    return page


class TwitterApiIoSource:
    name = "twitterapi_io"
    max_pages = 5

    def __init__(
        self,
        api_key: str,
        transport: httpx.BaseTransport | None = None,
        base_url: str = "https://api.twitterapi.io/",
        timeout: float = 10.0,
    ) -> None:
        self._client = httpx.Client(
            transport=transport,
            base_url=base_url,
            timeout=timeout,
            headers={"X-API-Key": api_key},
        )

    def close(self) -> None:
        self._client.close()

    def pages(self, list_id: int) -> Iterator[list[FetchedPost]]:
        cursor = ""
        while True:
            try:
                response = self._client.get(
                    "twitter/list/tweets", params={"listId": list_id, "cursor": cursor}
                )
            except httpx.TransportError:
                raise SourceUnavailableError("twitterapi_io: request failed") from None

            if response.status_code == 429:
                raise SourceRateLimitedError("twitterapi_io: rate limited", _retry_after(response))
            if response.status_code != 200:
                raise SourceUnavailableError(
                    f"twitterapi_io: unexpected status {response.status_code}"
                )
            try:
                body = response.json()
            except ValueError:
                raise SourcePayloadError("twitterapi_io: malformed response body") from None
            if isinstance(body, dict) and body.get("status") == "error":
                raise SourceUnavailableError("twitterapi_io: source reported an error")
            tweets = body.get("tweets") if isinstance(body, dict) else None
            if not isinstance(tweets, list):
                raise SourcePayloadError("twitterapi_io: malformed tweet payload")
            page = _map_posts(tweets)

            yield page

            next_cursor = body.get("next_cursor") or ""
            if not body.get("has_next_page") or not next_cursor:
                return
            cursor = next_cursor
