from typing import Any


def _as_id(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def reposted_x_id(raw: Any) -> int | None:
    if not isinstance(raw, dict):
        return None
    for key in ("retweetedTweet", "retweeted_tweet"):
        original = raw.get(key)
        if isinstance(original, dict):
            return _as_id(original.get("id"))
    tweet = raw.get("tweet")
    if isinstance(tweet, dict):
        references = tweet.get("referenced_tweets")
        if isinstance(references, list):
            for reference in references:
                if isinstance(reference, dict) and reference.get("type") == "retweeted":
                    return _as_id(reference.get("id"))
    return None


def origin_ids(x_id: int, raw: Any) -> set[int]:
    original = reposted_x_id(raw)
    return {x_id} if original is None else {x_id, original}
