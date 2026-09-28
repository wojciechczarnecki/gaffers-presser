from datetime import UTC, datetime, timedelta

from app.tweets.measure import (
    LatencyRecord,
    PollCounts,
    format_summary,
    poll_counts_to_json,
    read_records,
    record_to_json,
    summarise,
)

START = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)


def _record(source: str, x_id: int, latency: float) -> LatencyRecord:
    created_at = START
    return LatencyRecord(
        source=source,
        x_id=x_id,
        author_handle="synthetic_leaker",
        created_at=created_at,
        first_fetched_at=created_at + timedelta(seconds=latency),
        latency_seconds=latency,
    )


def test_summary_percentiles():
    records = [_record("twscrape", i, float(i)) for i in range(1, 101)]
    summaries = summarise(records, [PollCounts("twscrape", polls=100, failed_polls=0)])

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.source == "twscrape"
    assert summary.posts == 100
    assert summary.p50 == 50.0
    assert summary.p95 == 95.0
    assert summary.max == 100.0
    assert summary.polls == 100
    assert summary.failed_polls == 0


def test_summary_single_record():
    records = [_record("x_api", 1, 12.0)]
    summary = summarise(records, [PollCounts("x_api", polls=1, failed_polls=0)])[0]
    assert summary.p50 == 12.0
    assert summary.p95 == 12.0
    assert summary.max == 12.0


def test_summary_no_records_shows_dash():
    summary = summarise([], [PollCounts("twitterapi_io", polls=5, failed_polls=1)])[0]
    assert summary.posts == 0
    assert summary.p50 is None
    assert summary.p95 is None
    assert summary.max is None
    assert summary.polls == 5
    assert summary.failed_polls == 1


def test_two_sources_kept_apart():
    records = [_record("twscrape", 1, 10.0), _record("x_api", 2, 20.0)]
    summaries = summarise(
        records,
        [
            PollCounts("twscrape", polls=1, failed_polls=0),
            PollCounts("x_api", polls=1, failed_polls=0),
        ],
    )
    by_source = {summary.source: summary for summary in summaries}
    assert by_source["twscrape"].max == 10.0
    assert by_source["x_api"].max == 20.0


def test_format_summary_markdown_table():
    summaries = summarise(
        [_record("twscrape", 1, 5.0)], [PollCounts("twscrape", polls=1, failed_polls=0)]
    )
    text = format_summary(summaries, markdown=True)
    lines = text.splitlines()
    assert lines[0] == "| source | posts | p50 | p95 | max | polls | failed polls |"
    assert lines[1].startswith("|---|")
    assert "twscrape" in lines[2]
    assert "5.0" in lines[2]


def test_format_summary_plain_table():
    summaries = summarise([], [PollCounts("twscrape", polls=0, failed_polls=0)])
    text = format_summary(summaries, markdown=False)
    assert "source" in text
    assert "-" in text


def test_record_round_trips_through_json(tmp_path):
    path = tmp_path / "latency.jsonl"
    record = _record("twscrape", 42, 7.5)
    counts = PollCounts("twscrape", polls=3, failed_polls=1)
    path.write_text(
        record_to_json(record) + "\n" + poll_counts_to_json(counts) + "\n", encoding="utf-8"
    )

    records, poll_counts = read_records(path)

    assert records == [record]
    assert poll_counts == [counts]
