import logging
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import httpx

from app.tweets.sources.base import (
    FetchedPost,
    SourcePayloadError,
    SourceRateLimitedError,
    SourceUnavailableError,
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

_PARAMS = {
    "max_results": "20",
    "tweet.fields": "created_at,author_id,referenced_tweets",
    "expansions": "author_id",
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


def _to_post(tweet: dict, users_by_id: dict[str, dict]) -> FetchedPost:
    ref_types = {ref["type"] for ref in tweet.get("referenced_tweets", [])}
    author = users_by_id[tweet["author_id"]]
    return FetchedPost(
        x_id=int(tweet["id"]),
        author_handle=author["username"],
        text=tweet["text"],
        created_at=datetime.fromisoformat(tweet["created_at"].replace("Z", "+00:00")).astimezone(
            UTC
        ),
        is_repost=bool(ref_types & _REPOST_TYPES),
        is_reply=bool(ref_types & _REPLY_TYPES),
        raw={"tweet": tweet, "author": author},
    )


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
            try:
                users_by_id = {
                    user["id"]: user for user in body.get("includes", {}).get("users", [])
                }
                page = [_to_post(tweet, users_by_id) for tweet in body.get("data", [])]
            except (KeyError, TypeError, ValueError):
                raise SourcePayloadError("x_api: malformed tweet payload") from None

            yield page

            next_token = body.get("meta", {}).get("next_token")
            if not next_token:
                return
            pagination_token = next_token
