from app.retrieval.search import search
from tests.retrieval.helpers import PRICES, add_tweet


def test_results_carry_the_repost_fields(db):
    add_tweet(db, 1, "Saka injury news", is_repost=True, reposted_author_handle="origin")
    add_tweet(db, 2, "Saka injury update")

    response = search(db, "saka injury", "fulltext", prices=PRICES)

    by_id = {r.x_id: r for r in response.results}
    assert (by_id[1].is_repost, by_id[1].reposted_author_handle) == (True, "origin")
    assert (by_id[2].is_repost, by_id[2].reposted_author_handle) == (False, None)
