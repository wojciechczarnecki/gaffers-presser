import logging
from datetime import datetime

from app.tweets.sources.base import FetchedPost, TweetSource, merge_fetched

logger = logging.getLogger(__name__)

LAST_SEEN = "last seen post"


def collect_new(
    source: TweetSource, list_id: int, since_id: int | None, floor: datetime | None = None
) -> list[FetchedPost]:
    """Page back until the page holding since_id. With a floor, paging also covers an empty
    store or a gap left by downtime, but never past the floor (the start of the alert window);
    without one, an empty store takes the first page only."""
    collected: dict[int, FetchedPost] = {}
    pages_seen = 0
    for page in source.pages(list_id):
        pages_seen += 1
        # A post can become visible after a newer one, so an ID at or below since_id only
        # ends paging; only an entry head counts for it, an embedded post says nothing about
        # the timeline. Every post on the fetched pages is kept.
        for post in page:
            known = collected.get(post.x_id)
            collected[post.x_id] = post if known is None else merge_fetched(known, post)
        heads = [post for post in page if post.entry_head]
        if since_id is not None and any(post.x_id <= since_id for post in heads):
            stopped_by = LAST_SEEN
            break
        # a whole page before the floor, so one out-of-order post cannot end a catch-up early
        if floor is not None and heads and all(post.created_at < floor for post in heads):
            stopped_by = "window start"
            break
        if since_id is None and floor is None:
            stopped_by = "first page"
            break
        if pages_seen >= source.max_pages:
            stopped_by = "page limit"
            break
    else:
        stopped_by = "end of timeline"
    if pages_seen > 1 or stopped_by in ("window start", "page limit"):
        logger.info(
            "tweet poll paged back: source=%s pages=%s stopped_by=%s",
            source.name,
            pages_seen,
            stopped_by,
        )
    return sorted(collected.values(), key=lambda post: post.x_id)
