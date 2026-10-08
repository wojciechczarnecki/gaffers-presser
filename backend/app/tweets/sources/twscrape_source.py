import asyncio
import json
import logging
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import NoReturn

os.environ.setdefault("TWS_TELEMETRY", "0")

import twscrape  # noqa: E402
from twscrape import API, NoAccountError  # noqa: E402

from app.tweets.sources.base import (
    FetchedPost,
    SourcePayloadError,
    SourceRateLimitedError,
    SourceUnavailableError,
)

logger = logging.getLogger(__name__)

twscrape.set_log_level("ERROR")

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

_QUEUE = "ListLatestTweetsTimeline"
_MEMBERS_QUEUE = "ListMembers"
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


def _result_id(result) -> int | None:
    if not isinstance(result, dict):
        return None
    rest_id = result.get("rest_id")
    if rest_id is None and isinstance(result.get("tweet"), dict):
        rest_id = result["tweet"].get("rest_id")
    try:
        return int(rest_id)
    except (TypeError, ValueError):
        return None


def _item_result_id(item) -> int | None:
    content = item.get("itemContent") if isinstance(item, dict) else None
    results = content.get("tweet_results") if isinstance(content, dict) else None
    return _result_id(results.get("result")) if isinstance(results, dict) else None


def _entry_groups(page_dict: dict) -> list[list[int]]:
    try:
        instructions = page_dict["data"]["list"]["tweets_timeline"]["timeline"]["instructions"]
    except (KeyError, TypeError):
        return []
    groups: list[list[int]] = []
    for instruction in instructions:
        if not isinstance(instruction, dict):
            continue
        entries = list(instruction.get("entries") or [])
        if isinstance(instruction.get("entry"), dict):
            entries.append(instruction["entry"])
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_id = str(entry.get("entryId", ""))
            content = entry.get("content")
            if entry_id.startswith(("cursor-", "promoted-")) or not isinstance(content, dict):
                continue
            if content.get("items") is not None:
                ids = [
                    _item_result_id(wrapper.get("item"))
                    for wrapper in content["items"]
                    if isinstance(wrapper, dict)
                ]
            else:
                ids = [_item_result_id(content)]
            group = [x_id for x_id in ids if x_id is not None]
            if group:
                groups.append(group)
    return groups


def _member_handles(page_dict) -> list[str]:
    handles = []
    timeline = page_dict["data"]["list"]["members_timeline"]["timeline"]
    for instruction in timeline["instructions"]:
        for entry in instruction.get("entries") or []:
            if not str(entry.get("entryId", "")).startswith("user-"):
                continue
            result = entry["content"]["itemContent"]["user_results"]["result"]
            handle = (result.get("core") or {}).get("screen_name") or (
                result.get("legacy") or {}
            ).get("screen_name")
            if handle:
                handles.append(str(handle))
    return handles


def _to_post(tweet, in_entry: set[int] | None, heads: set[int]) -> FetchedPost:
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
        embedded=in_entry is not None and tweet.id not in in_entry,
        entry_head=in_entry is None or tweet.id in heads,
        # A repost's own object may carry the reposted post's quote; the repost counts as
        # its original author, never as a quote.
        quoted_x_id=(
            tweet.quotedTweet.id
            if tweet.quotedTweet is not None and tweet.retweetedTweet is None
            else None
        ),
    )


class TwscrapeSource:
    name = "twscrape"
    max_pages = 50

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

    def _raise_no_account(self, queue: str) -> NoReturn:
        next_available = self._runner.run(self._api.pool.next_available_at(queue))
        if next_available is None:
            raise SourceUnavailableError("twscrape: no active account") from None
        raise SourceRateLimitedError(
            "twscrape: rate limited", _retry_after_from_next_available(next_available)
        ) from None

    def members(self, list_id: int) -> list[str]:
        gen = self._api.list_members_raw(list_id)
        handles: list[str] = []
        try:
            while True:
                try:
                    response = self._runner.run(anext(gen))
                except StopAsyncIteration:
                    break
                except NoAccountError:
                    self._raise_no_account(_MEMBERS_QUEUE)
                try:
                    handles.extend(_member_handles(response.json()))
                except Exception as exc:
                    raise SourcePayloadError("twscrape: malformed members page") from exc
        finally:
            self._runner.run(gen.aclose())
        if not handles:
            raise SourcePayloadError("twscrape: empty member list")
        return handles

    def pages(self, list_id: int) -> Iterator[list[FetchedPost]]:
        gen = self._api.list_timeline_raw(list_id)
        try:
            while True:
                try:
                    response = self._runner.run(anext(gen))
                except StopAsyncIteration:
                    return
                except NoAccountError:
                    self._raise_no_account(_QUEUE)

                try:
                    page_dict = response.json()
                    tweets = list(twscrape.parse_tweets(page_dict))
                    groups = _entry_groups(page_dict)
                except Exception as exc:
                    raise SourcePayloadError("twscrape: malformed page") from exc

                in_entry: set[int] | None = {x_id for group in groups for x_id in group}
                heads = {max(group) for group in groups}
                if tweets and not any(tweet.id in heads for tweet in tweets):
                    logger.warning("twscrape: no timeline entries recognised on a page")
                    in_entry, heads = None, set()
                yield [_to_post(tweet, in_entry, heads) for tweet in tweets]
        finally:
            self._runner.run(gen.aclose())
