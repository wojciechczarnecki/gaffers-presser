import logging
from datetime import datetime

from app.tweets.sources.base import FetchedPost, TweetSource

logger = logging.getLogger(__name__)


def collect_new(
    source: TweetSource, list_id: int, since_id: int | None, floor: datetime | None = None
) -> list[FetchedPost]:
    """Page back until the page holding since_id. With a floor, paging also covers an empty
    store or a gap left by downtime, but never past the floor (the start of the alert window);
    without one, an empty store takes the first page only."""
    collected: dict[int, FetchedPost] = {}
    pages_seen = 0
    stopped_by = "end of timeline"
    for page in source.pages(list_id):
        pages_seen += 1
        stop = False
        # A post can become visible after a newer one, so an ID at or below since_id only
        # ends paging; every post on the fetched pages is kept and the store dedupes.
        for post in page:
            if since_id is not None and post.x_id <= since_id:
                stop = True
            collected.setdefault(post.x_id, post)
        if stop:
            stopped_by = None
            break
        if floor is not None and any(post.created_at < floor for post in page):
            stopped_by = "window start"
            break
        if since_id is None and floor is None:
            stopped_by = None
            break
        if pages_seen >= source.max_pages:
            stopped_by = "page limit"
            break
    if stopped_by is not None or pages_seen > 1:
        logger.info(
            "tweet poll paged back: source=%s pages=%s stopped_by=%s",
            source.name,
            pages_seen,
            stopped_by or "last seen post",
        )
    return sorted(collected.values(), key=lambda post: post.x_id)
