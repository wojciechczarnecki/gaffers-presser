import logging
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import httpx

from app.tweets.sources.base import (
    FetchedPost,
    MembershipNotSupportedError,
    SourcePayloadError,
    SourceRateLimitedError,
    SourceUnavailableError,
)

logger = logging.getLogger(__name__)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

_PARAMS = {
    "max_results": "20",
    "tweet.fields": "created_at,author_id,referenced_tweets",
    "expansions": "author_id,referenced_tweets.id.author_id",
    "user.fields": "username",
}

_REPOST_TYPES = {"retweeted"}
_REPLY_TYPES = {"replied_to"}


def _retry_after(response: httpx.Response, now: float) -> float | None:
    header = response.headers.get("x-rate-limit-reset")
    if header is None:
        return None
    try:
        return max(0.0, float(header) - now)
    except ValueError:
        return None


def _retweeted_author(
    tweet: dict, users_by_id: dict[str, dict], tweets_by_id: dict[str, dict]
) -> dict | None:
    for ref in tweet.get("referenced_tweets", []):
        if ref["type"] not in _REPOST_TYPES:
            continue
        original = tweets_by_id.get(ref["id"])
        if original is not None:
            return users_by_id.get(original.get("author_id"))
    return None


def _to_post(
    tweet: dict, users_by_id: dict[str, dict], tweets_by_id: dict[str, dict]
) -> FetchedPost:
    ref_types = {ref["type"] for ref in tweet.get("referenced_tweets", [])}
    author = users_by_id[tweet["author_id"]]
    original_author = _retweeted_author(tweet, users_by_id, tweets_by_id)
    raw = {"tweet": tweet, "author": author}
    if original_author is not None:
        raw["retweeted_author"] = original_author
    return FetchedPost(
        x_id=int(tweet["id"]),
        author_handle=author["username"],
        text=tweet["text"],
        created_at=datetime.fromisoformat(tweet["created_at"].replace("Z", "+00:00")).astimezone(
            UTC
        ),
        is_repost=bool(ref_types & _REPOST_TYPES),
        is_reply=bool(ref_types & _REPLY_TYPES),
        raw=raw,
        reposted_author_handle=original_author["username"] if original_author else None,
    )


def _map_posts(
    tweets: list, users_by_id: dict[str, dict], tweets_by_id: dict[str, dict]
) -> list[FetchedPost]:
    page = []
    for tweet in tweets:
        try:
            page.append(_to_post(tweet, users_by_id, tweets_by_id))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            logger.warning("x_api: skipped a post that failed to map: %s", type(exc).__name__)
    return page


class XApiSource:
    name = "x_api"
    max_pages = 3

    def __init__(
        self,
        bearer_token: str,
        transport: httpx.BaseTransport | None = None,
        base_url: str = "https://api.x.com/",
        timeout: float = 10.0,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._client = httpx.Client(
            transport=transport,
            base_url=base_url,
            timeout=timeout,
            headers={"Authorization": f"Bearer {bearer_token}"},
        )
        self._now = now

    def close(self) -> None:
        self._client.close()

    def members(self, list_id: int) -> list[str]:
        raise MembershipNotSupportedError("x_api: list membership is not supported")

    def pages(self, list_id: int) -> Iterator[list[FetchedPost]]:
        pagination_token: str | None = None
        while True:
            params = dict(_PARAMS)
            if pagination_token:
                params["pagination_token"] = pagination_token
            try:
                response = self._client.get(f"2/lists/{list_id}/tweets", params=params)
            except httpx.TransportError:
                raise SourceUnavailableError("x_api: request failed") from None

            if response.status_code == 429:
                raise SourceRateLimitedError(
                    "x_api: rate limited", _retry_after(response, self._now())
                )
            if response.status_code != 200:
                raise SourceUnavailableError(f"x_api: unexpected status {response.status_code}")
            try:
                body = response.json()
            except ValueError:
                raise SourcePayloadError("x_api: malformed response body") from None
            if not isinstance(body, dict):
                raise SourcePayloadError("x_api: malformed response body")
            if "data" not in body and body.get("errors"):
                raise SourceUnavailableError("x_api: source reported an error")
            try:
                includes = body.get("includes", {})
                users_by_id = {user["id"]: user for user in includes.get("users", [])}
                tweets_by_id = {item["id"]: item for item in includes.get("tweets", [])}
            except (KeyError, TypeError, AttributeError):
                raise SourcePayloadError("x_api: malformed user payload") from None
            tweets = body.get("data", [])
            if not isinstance(tweets, list):
                raise SourcePayloadError("x_api: malformed tweet payload")
            page = _map_posts(tweets, users_by_id, tweets_by_id)

            yield page

            next_token = body.get("meta", {}).get("next_token")
            if not next_token:
                return
            pagination_token = next_token
