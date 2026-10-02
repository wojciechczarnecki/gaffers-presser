from datetime import timedelta

from app.alerts.latency import PostLatency, format_report, percentile, summarize
from tests.corroboration.helpers import NOW


def latency(fetch: float, extract: float, accept: float, x_id: int = 1) -> PostLatency:
    created = NOW
    fetched = created + timedelta(seconds=fetch)
    extracted = fetched + timedelta(seconds=extract)
    return PostLatency(x_id, created, fetched, extracted, extracted + timedelta(seconds=accept))


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
    assert all(line.split()[-3:] == ["-", "-", "-"] for line in lines[3:])


def test_report_lists_every_leg_with_three_statistics():
    lines = format_report("2026/27:gw6", [latency(10, 15, 6), latency(30, 10, 20)])

    assert lines[1] == "Posts: 2"
    assert [line.split("  ")[0].strip() for line in lines[3:]] == [
        "post -> first fetch",
        "fetch -> extraction done",
        "extraction -> accepted",
        "total",
    ]
    assert lines[3].split()[-6:] == ["10.0", "s", "30.0", "s", "30.0", "s"]
