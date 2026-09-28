from app.tweets.sources.paging import collect_new
from tests.tweets.fakes import FakeSource, post


def test_since_id_none_fetches_first_page_only():
    source = FakeSource([[post(3), post(2), post(1)], [post(0)]])
    result = collect_new(source, list_id=1, since_id=None)
    assert [p.x_id for p in result] == [1, 2, 3]
    assert source.pull_count == 1


def test_stops_after_page_holding_last_seen_id():
    source = FakeSource(
        [
            [post(5), post(4)],
            [post(3), post(2)],
            [post(1)],
        ]
    )
    result = collect_new(source, list_id=1, since_id=2)
    assert [p.x_id for p in result] == [3, 4, 5]
    assert source.pull_count == 2


def test_stops_at_max_pages_and_logs(caplog):
    source = FakeSource([[post(3)], [post(2)], [post(1)]], max_pages=2)
    with caplog.at_level("INFO"):
        result = collect_new(source, list_id=1, since_id=0)
    assert [p.x_id for p in result] == [2, 3]
    assert source.pull_count == 2
    assert "page limit" in caplog.text


def test_iterator_end_stops_collection():
    source = FakeSource([[post(3)], [post(2)]])
    result = collect_new(source, list_id=1, since_id=0)
    assert [p.x_id for p in result] == [2, 3]


def test_duplicates_within_a_poll_are_dropped():
    source = FakeSource([[post(3), post(3)], [post(1)]])
    result = collect_new(source, list_id=1, since_id=0)
    assert [p.x_id for p in result] == [1, 3]


def test_no_new_posts_returns_empty_list():
    source = FakeSource([[post(1)], [post(0)]])
    result = collect_new(source, list_id=1, since_id=1)
    assert result == []
