from datetime import UTC, datetime

import httpx
from sqlmodel import Session

from app.tweets.classes import post_classes, quoted_authors
from app.tweets.sources.twscrape_source import TwscrapeSource
from app.tweets.store import store_posts
from tests.tweets.fakes import post
from tests.tweets.membership_helpers import set_members
from tests.tweets.payloads import load
from tests.tweets.sources.test_twscrape_source import FakeApi

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def _store(db, *posts):
    with Session(db) as session, session.begin():
        store_posts(session, list(posts), "twscrape", NOW)


def _classes(db, *x_ids):
    with Session(db) as session:
        return post_classes(session, list(x_ids))


def test_no_snapshot_every_post_is_a_list_post(db):
    _store(db, post(1, author_handle="anyone"), post(2, author_handle="other", embedded=True))
    assert _classes(db, 1, 2) == {1: "list", 2: "list"}


def test_unsupported_snapshot_every_post_is_a_list_post(db):
    _store(db, post(1, author_handle="anyone"))
    set_members(db, None)
    assert _classes(db, 1) == {1: "list"}


def test_class_follows_the_current_snapshot_both_ways(db):
    _store(db, post(1, author_handle="Outsider", embedded=True))
    set_members(db, ["someone_else"])
    assert _classes(db, 1) == {1: "context"}
    set_members(db, ["someone_else", "outsider"])
    assert _classes(db, 1) == {1: "list"}
    set_members(db, ["someone_else"])
    assert _classes(db, 1) == {1: "context"}


def test_repost_counts_by_its_reposting_member(db):
    _store(
        db,
        post(
            1,
            author_handle="member",
            is_repost=True,
            reposted_author_handle="outsider",
        ),
    )
    set_members(db, ["member"])
    assert _classes(db, 1) == {1: "list"}
    set_members(db, ["outsider"])
    assert _classes(db, 1) == {1: "context"}


def test_quote_by_context_post_does_not_make_a_quoted_post(db):
    _store(
        db,
        post(1, author_handle="outsider_a", quoted_x_id=2),
        post(2, author_handle="outsider_b"),
        post(3, author_handle="member", quoted_x_id=4),
        post(4, author_handle="outsider_c"),
    )
    set_members(db, ["member"])
    assert _classes(db, 1, 2, 3, 4) == {1: "context", 2: "context", 3: "list", 4: "quoted"}


def test_handles_compared_case_insensitively(db):
    _store(db, post(1, author_handle="MiXeD"))
    set_members(db, ["MIXED"])
    assert _classes(db, 1) == {1: "list"}


def test_quote_relation_follows_the_snapshot(db):
    _store(db, post(3, author_handle="member", quoted_x_id=4), post(4, author_handle="out"))
    set_members(db, ["member"])
    assert _classes(db, 4) == {4: "quoted"}
    set_members(db, ["nobody"])
    assert _classes(db, 4) == {4: "context"}


def test_classes_of_nothing(db):
    assert _classes(db) == {}
    with Session(db) as session:
        assert quoted_authors(session, []) == {}


def test_recorded_pages_classified(db):
    api = FakeApi(
        [
            httpx.Response(200, json=load("twscrape-page-conversation")),
            httpx.Response(200, json=load("twscrape-page-conversation-2")),
        ]
    )
    source = TwscrapeSource("dedicated", "auth_token=a; ct0=b", ":unused:", api=api)
    try:
        pages = list(source.pages(1))
    finally:
        source.close()
    _store(db, *[p for page in pages for p in page])
    set_members(db, ["synthetic_leaker_1", "synthetic_leaker_2", "synthetic_leaker_3"])
    classes = _classes(db, 4010, 1500, 4006, 1700, 4014, 4004, 4008, 4012)
    assert classes == {
        4010: "list",
        1500: "quoted",
        4006: "context",
        1700: "context",
        4014: "list",
        4004: "list",
        4008: "list",
        4012: "list",
    }
    assert 1800 not in _classes(db, 1800)


def test_backfilled_quote_relation_classifies_the_same(db):
    _store(db, post(10, author_handle="member"), post(11, author_handle="old_outsider"))
    set_members(db, ["member"])
    assert _classes(db, 11) == {11: "context"}
    with db.begin() as conn:
        conn.exec_driver_sql("UPDATE tweet SET quoted_x_id = 11 WHERE x_id = 10")
    assert _classes(db, 11) == {11: "quoted"}


def test_quoted_authors(db):
    _store(
        db,
        post(1, author_handle="member", quoted_x_id=2),
        post(2, author_handle="Outsider"),
        post(3, author_handle="member", quoted_x_id=999),
        post(4, author_handle="member"),
    )
    with Session(db) as session:
        assert quoted_authors(session, [1, 3, 4]) == {1: "Outsider"}
