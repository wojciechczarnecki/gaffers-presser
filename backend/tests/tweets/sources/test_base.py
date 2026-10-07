import pytest

from app.tweets.sources.base import merge_fetched
from tests.tweets.fakes import post

TIMELINE = post(1, text="timeline", entry_head=False, raw={"copy": "timeline"})
EMBEDDED = post(1, text="embedded", embedded=True, entry_head=False, quoted_x_id=9)


@pytest.mark.parametrize(("first", "second"), [(TIMELINE, EMBEDDED), (EMBEDDED, TIMELINE)])
def test_merge_keeps_the_timeline_copy_and_the_known_quote(first, second):
    merged = merge_fetched(first, second)
    assert (merged.text, merged.raw) == ("timeline", {"copy": "timeline"})
    assert merged.embedded is False
    assert merged.entry_head is False
    assert merged.quoted_x_id == 9


@pytest.mark.parametrize("head_first", [True, False])
def test_merge_is_a_head_when_either_copy_is(head_first):
    head, other = post(1, entry_head=True), post(1, entry_head=False)
    first, second = (head, other) if head_first else (other, head)
    assert merge_fetched(first, second).entry_head is True


def test_merge_of_two_embedded_copies_stays_embedded():
    merged = merge_fetched(
        post(1, text="a", embedded=True, entry_head=False),
        post(1, text="b", embedded=True, entry_head=False, quoted_x_id=4),
    )
    assert (merged.text, merged.embedded, merged.quoted_x_id) == ("a", True, 4)
