from datetime import UTC, datetime, timedelta

import pytest

from app.tweets.sources.base import SourceRateLimitedError
from app.tweets.sources.paging import collect_new
from tests.tweets.fakes import FakeSource, post

FLOOR = datetime(2026, 10, 3, 10, 0, tzinfo=UTC)


def dated(x_id: int, hours_after_floor: float):
    return post(x_id, created_at=FLOOR + timedelta(hours=hours_after_floor))


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
    assert [p.x_id for p in result] == [2, 3, 4, 5]
    assert source.pull_count == 2


def test_late_post_below_last_seen_is_kept():
    source = FakeSource([[post(7), post(4), post(5)], [post(3)]])
    result = collect_new(source, list_id=1, since_id=5)
    assert [p.x_id for p in result] == [4, 5, 7]
    assert source.pull_count == 1


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


def test_no_new_posts_returns_only_the_seen_page():
    source = FakeSource([[post(1)], [post(0)]])
    result = collect_new(source, list_id=1, since_id=1)
    assert [p.x_id for p in result] == [1]
    assert source.pull_count == 1


def test_empty_page_returns_empty_list():
    source = FakeSource([[]])
    assert collect_new(source, list_id=1, since_id=1) == []


def test_regular_poll_with_a_floor_is_unchanged_and_quiet(caplog):
    source = FakeSource([[dated(5, 30), dated(4, 29)], [dated(3, 28)]], max_pages=50)
    with caplog.at_level("INFO"):
        result = collect_new(source, list_id=1, since_id=4, floor=FLOOR)
    assert [p.x_id for p in result] == [4, 5]
    assert source.pull_count == 1
    assert "paged back" not in caplog.text


def test_catch_up_pages_past_the_old_page_limit_to_the_last_seen_post(caplog):
    pages = [[dated(100 - i, 50 - i)] for i in range(8)]  # x_ids 100..93, all after the floor
    source = FakeSource(pages, max_pages=50)
    with caplog.at_level("INFO"):
        result = collect_new(source, list_id=1, since_id=93, floor=FLOOR)
    assert [p.x_id for p in result] == list(range(93, 101))
    assert source.pull_count == 8
    assert "pages=8 stopped_by=last seen post" in caplog.text


def test_catch_up_stops_at_the_floor_when_the_last_seen_post_is_older(caplog):
    source = FakeSource(
        [[dated(9, 3), dated(8, 2)], [dated(7, 1), dated(6, -1)], [dated(5, -2)]], max_pages=50
    )
    with caplog.at_level("INFO"):
        result = collect_new(source, list_id=1, since_id=2, floor=FLOOR)
    assert [p.x_id for p in result] == [5, 6, 7, 8, 9]
    assert source.pull_count == 3
    assert "pages=3 stopped_by=window start" in caplog.text


def test_one_old_post_on_a_fresh_page_does_not_end_the_catch_up():
    source = FakeSource([[dated(9, 3), dated(8, -30)], [dated(7, 2)], [dated(6, -1)]])
    result = collect_new(source, list_id=1, since_id=6, floor=FLOOR)
    assert [p.x_id for p in result] == [6, 7, 8, 9]
    assert source.pull_count == 3


def test_a_short_timeline_without_the_last_seen_post_is_quiet(caplog):
    source = FakeSource([[dated(9, 3)]], max_pages=50)
    with caplog.at_level("INFO"):
        result = collect_new(source, list_id=1, since_id=1, floor=FLOOR)
    assert [p.x_id for p in result] == [9]
    assert "paged back" not in caplog.text


def test_empty_store_with_a_floor_pages_back_to_the_floor():
    source = FakeSource([[dated(9, 3)], [dated(8, 2)], [dated(7, -1)], [dated(6, -2)]])
    result = collect_new(source, list_id=1, since_id=None, floor=FLOOR)
    assert [p.x_id for p in result] == [7, 8, 9]
    assert source.pull_count == 3


def test_catch_up_stops_at_the_page_limit_and_logs(caplog):
    source = FakeSource([[dated(9, 3)], [dated(8, 2)], [dated(7, 1)]], max_pages=2)
    with caplog.at_level("INFO"):
        result = collect_new(source, list_id=1, since_id=None, floor=FLOOR)
    assert [p.x_id for p in result] == [8, 9]
    assert "pages=2 stopped_by=page limit" in caplog.text


def test_catch_up_logs_the_end_of_the_timeline(caplog):
    source = FakeSource([[dated(9, 3)], [dated(8, 2)]], max_pages=50)
    with caplog.at_level("INFO"):
        result = collect_new(source, list_id=1, since_id=1, floor=FLOOR)
    assert [p.x_id for p in result] == [8, 9]
    assert "pages=2 stopped_by=end of timeline" in caplog.text


def test_rate_limit_during_catch_up_propagates():
    source = FakeSource([[dated(9, 3)], SourceRateLimitedError("limited", 60.0)], max_pages=50)
    with pytest.raises(SourceRateLimitedError):
        collect_new(source, list_id=1, since_id=1, floor=FLOOR)


def test_embedded_old_post_does_not_end_paging():
    source = FakeSource(
        [
            [post(9), post(2, embedded=True, entry_head=False)],
            [post(6), post(5)],
            [post(4)],
        ]
    )
    result = collect_new(source, list_id=1, since_id=5)
    assert source.pull_count == 2
    assert [p.x_id for p in result] == [2, 5, 6, 9]


def test_old_non_head_module_item_does_not_end_paging():
    source = FakeSource(
        [
            [post(9), post(3, entry_head=False)],
            [post(6), post(5)],
        ]
    )
    collect_new(source, list_id=1, since_id=5)
    assert source.pull_count == 2


def test_floor_rule_reads_entry_heads_only():
    old = FLOOR - timedelta(days=2)
    source = FakeSource(
        [
            [post(2, created_at=old), post(9, created_at=FLOOR, embedded=True, entry_head=False)],
            [post(1, created_at=old)],
        ]
    )
    collect_new(source, list_id=1, since_id=None, floor=FLOOR)
    assert source.pull_count == 1


def test_page_without_heads_continues():
    source = FakeSource(
        [
            [post(2, embedded=True, entry_head=False)],
            [post(1)],
        ]
    )
    collect_new(source, list_id=1, since_id=5, floor=FLOOR + timedelta(days=30))
    assert source.pull_count == 2


def test_duplicate_across_pages_keeps_timeline_copy():
    for pages in (
        [[post(5, embedded=True, entry_head=False)], [post(5, quoted_x_id=3)]],
        [[post(5, quoted_x_id=3)], [post(5, embedded=True, entry_head=False)]],
    ):
        result = collect_new(FakeSource(pages), list_id=1, since_id=0, floor=None)
        assert len(result) == 1
        assert result[0].embedded is False
        assert result[0].quoted_x_id == 3
        assert result[0].entry_head is True
