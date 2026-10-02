from app.tweets.reposts import origin_ids, reposted_x_id
from tests.tweets.sources.test_repost_authors import _twitterapi_page, _twscrape_page, _x_api_page


def test_reposted_x_id_per_source():
    twscrape_repost, twscrape_reply, twscrape_normal = _twscrape_page()
    assert twscrape_repost.is_repost
    assert reposted_x_id(twscrape_repost.raw) == 9999000000000000008
    assert reposted_x_id(twscrape_normal.raw) is None

    api_io_repost, _, api_io_normal = _twitterapi_page()
    assert reposted_x_id(api_io_repost.raw) == 500
    assert reposted_x_id(api_io_normal.raw) is None

    x_api_repost, _, x_api_normal = _x_api_page()
    assert reposted_x_id(x_api_repost.raw) == 500
    assert reposted_x_id(x_api_normal.raw) is None


def test_reposted_x_id_of_a_reply_or_a_quote_is_none():
    raw = {"tweet": {"referenced_tweets": [{"type": "replied_to", "id": "7"}]}}
    assert reposted_x_id(raw) is None
    quoted = {"tweet": {"referenced_tweets": [{"type": "quoted", "id": "7"}]}}
    assert reposted_x_id(quoted) is None


def test_empty_or_malformed_raw_is_none():
    for raw in (
        {},
        None,
        {"retweetedTweet": None},
        {"retweetedTweet": {}},
        {"retweeted_tweet": "x"},
    ):
        assert reposted_x_id(raw) is None
    assert reposted_x_id({"tweet": {"referenced_tweets": [{"type": "retweeted"}]}}) is None
    assert (
        reposted_x_id({"tweet": {"referenced_tweets": [{"type": "retweeted", "id": "abc"}]}})
        is None
    )
    assert reposted_x_id({"tweet": {"referenced_tweets": "oops"}}) is None


def test_origin_ids_hold_the_post_and_its_original():
    assert origin_ids(20, {"retweeted_tweet": {"id": 10}}) == {20, 10}
    assert origin_ids(20, {}) == {20}
