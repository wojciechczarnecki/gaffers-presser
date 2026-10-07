from datetime import timedelta

from sqlmodel import Session

from app.alerts.latency import PostLatency, format_report, percentile, post_latencies, summarize
from tests.alerts.test_cli import REAL, latency_post, sent_alert
from tests.alerts.test_store import post
from tests.corroboration.helpers import NOW
from tests.tweets.membership_helpers import set_members


def latency(
    fetch: float, extract: float, accept: float, x_id: int = 1, kind: str = "breaking"
) -> PostLatency:
    created = NOW
    fetched = created + timedelta(seconds=fetch)
    extracted = fetched + timedelta(seconds=extract)
    return PostLatency(
        x_id, created, fetched, extracted, extracted + timedelta(seconds=accept), kind
    )


def test_percentiles_and_legs():
    assert percentile([5.0], 95) == 5.0
    assert percentile([1, 2, 3, 4], 50) == 2
    assert percentile([1, 2, 3, 4], 95) == 4
    assert percentile(list(range(1, 101)), 95) == 95
    assert percentile([3, 1, 2], 50) == 2

    rows = [latency(10, 15, 6, 1), latency(30, 10, 20, 2), latency(20, 30, 51, 3)]
    legs = summarize(rows)

    assert legs is not None
    assert [(leg.p50, leg.p95, leg.maximum) for leg in legs] == [
        (20, 30, 30),
        (15, 30, 30),
        (20, 51, 51),
        (60, 101, 101),
    ]


def test_empty_report_has_count_zero_and_dashes():
    assert summarize([]) is None
    lines = format_report("2026/27:gw6", [])

    assert lines[0] == "Alert deadline: 2026/27:gw6"
    assert lines[1] == "Posts: 0"
    assert len(lines) == 7
    assert all(line.split()[-3:] == ["-", "-", "-"] for line in lines[3:])


def test_report_lists_every_leg_with_three_statistics():
    lines = format_report("2026/27:gw6", [latency(10, 15, 6), latency(30, 10, 20)])

    assert lines[1] == "Posts: 2"
    assert [line.split("  ")[0].strip() for line in lines[3:7]] == [
        "post -> first fetch",
        "fetch -> extraction done",
        "extraction -> accepted",
        "total",
    ]
    assert lines[3].split()[-6:] == ["10.0", "s", "30.0", "s", "30.0", "s"]


def test_report_splits_legs_per_alert_kind():
    digest_post = latency(600, 300, 7200, x_id=1, kind="digest")
    breaking_post = latency(10, 15, 6, x_id=2, kind="breaking")

    lines = format_report("2026/27:gw6", [breaking_post, digest_post])

    assert lines[1] == "Posts: 2"
    assert lines[6].split()[-6:] == ["31.0", "s", "8100.0", "s", "8100.0", "s"]
    digest_at = lines.index("Kind: digest  posts: 1")
    breaking_at = lines.index("Kind: breaking  posts: 1")
    assert digest_at < breaking_at
    assert "Kind: news" not in "\n".join(lines)
    assert lines[digest_at + 5].split()[-6:] == ["8100.0", "s", "8100.0", "s", "8100.0", "s"]
    assert lines[breaking_at + 4].split()[0:2] == ["extraction", "->"]
    assert lines[breaking_at + 4].split()[-6:] == ["6.0", "s", "6.0", "s", "6.0", "s"]
    assert lines[breaking_at + 5].split()[-6:] == ["31.0", "s", "31.0", "s", "31.0", "s"]


def test_post_latencies_leave_out_a_quoted_post(db):
    from tests.corroboration.helpers import seed_reference

    seed_reference(db)
    latency_post(db, 1, 10, 5, created=NOW)
    latency_post(db, 2, 30, 5, created=NOW - timedelta(days=1))
    with db.begin() as conn:
        conn.exec_driver_sql("UPDATE tweet SET author_handle = 'outsider' WHERE x_id = 2")
        conn.exec_driver_sql("UPDATE tweet SET quoted_x_id = 2 WHERE x_id = 1")
    set_members(db, ["a1"])
    sent_alert(db, "k1", 1, 60, REAL, extra=(post(2),))

    with Session(db) as session:
        rows = post_latencies(session, REAL.key)

    assert [row.x_id for row in rows] == [1]
