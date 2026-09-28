import logging

from app.tweets.sources.base import FetchedPost, TweetSource

logger = logging.getLogger(__name__)


def collect_new(source: TweetSource, list_id: int, since_id: int | None) -> list[FetchedPost]:
    collected: dict[int, FetchedPost] = {}
    pages_seen = 0
    for page in source.pages(list_id):
        pages_seen += 1
        stop = False
        # A post can become visible after a newer one, so an ID at or below since_id only
        # ends paging; every post on the fetched pages is kept and the store dedupes.
        for post in page:
            if since_id is not None and post.x_id <= since_id:
                stop = True
            collected.setdefault(post.x_id, post)
        if since_id is None:
            break
        if stop:
            break
        if pages_seen >= source.max_pages:
            logger.info("tweet poll reached the page limit: source=%s", source.name)
            break
    return sorted(collected.values(), key=lambda post: post.x_id)
