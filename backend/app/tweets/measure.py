import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class LatencyRecord:
    source: str
    x_id: int
    author_handle: str
    created_at: datetime
    first_fetched_at: datetime
    latency_seconds: float


@dataclass(frozen=True)
class PollCounts:
    source: str
    polls: int
    failed_polls: int


@dataclass(frozen=True)
class SourceSummary:
    source: str
    posts: int
    p50: float | None
    p95: float | None
    max: float | None
    polls: int
    failed_polls: int


def record_to_json(record: LatencyRecord) -> str:
    return json.dumps(
        {
            "source": record.source,
            "x_id": str(record.x_id),
            "author_handle": record.author_handle,
            "created_at": record.created_at.isoformat(),
            "first_fetched_at": record.first_fetched_at.isoformat(),
            "latency_seconds": record.latency_seconds,
        }
    )


def poll_counts_to_json(counts: PollCounts) -> str:
    return json.dumps(
        {"source": counts.source, "failed_polls": counts.failed_polls, "polls": counts.polls}
    )


def read_records(path: Path) -> tuple[list[LatencyRecord], list[PollCounts]]:
    records: list[LatencyRecord] = []
    poll_counts: list[PollCounts] = []
    text = Path(path).read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if "x_id" in obj:
            records.append(
                LatencyRecord(
                    source=obj["source"],
                    x_id=int(obj["x_id"]),
                    author_handle=obj["author_handle"],
                    created_at=datetime.fromisoformat(obj["created_at"]),
                    first_fetched_at=datetime.fromisoformat(obj["first_fetched_at"]),
                    latency_seconds=obj["latency_seconds"],
                )
            )
        else:
            poll_counts.append(
                PollCounts(
                    source=obj["source"],
                    polls=obj["polls"],
                    failed_polls=obj["failed_polls"],
                )
            )
    return records, poll_counts


def _percentile(sorted_values: list[float], p: float) -> float:
    n = len(sorted_values)
    index = math.ceil(p / 100 * n) - 1
    return sorted_values[max(0, min(index, n - 1))]


def summarise(records: list[LatencyRecord], poll_counts: list[PollCounts]) -> list[SourceSummary]:
    by_source: dict[str, list[float]] = {}
    for record in records:
        by_source.setdefault(record.source, []).append(record.latency_seconds)
    counts_by_source = {counts.source: counts for counts in poll_counts}

    sources = sorted(set(by_source) | set(counts_by_source))
    summaries = []
    for source in sources:
        latencies = sorted(by_source.get(source, []))
        counts = counts_by_source.get(source)
        polls = counts.polls if counts is not None else 0
        failed_polls = counts.failed_polls if counts is not None else 0
        if latencies:
            p50 = _percentile(latencies, 50)
            p95 = _percentile(latencies, 95)
            max_latency = latencies[-1]
        else:
            p50 = p95 = max_latency = None
        summaries.append(
            SourceSummary(
                source=source,
                posts=len(latencies),
                p50=p50,
                p95=p95,
                max=max_latency,
                polls=polls,
                failed_polls=failed_polls,
            )
        )
    return summaries


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def format_summary(summaries: list[SourceSummary], markdown: bool = False) -> str:
    headers = ["source", "posts", "p50", "p95", "max", "polls", "failed polls"]
    rows = [
        [
            summary.source,
            str(summary.posts),
            _fmt(summary.p50),
            _fmt(summary.p95),
            _fmt(summary.max),
            str(summary.polls),
            str(summary.failed_polls),
        ]
        for summary in summaries
    ]
    if markdown:
        lines = [
            "| " + " | ".join(headers) + " |",
            "|" + "|".join(["---"] * len(headers)) + "|",
        ]
        for row in rows:
            lines.append("| " + " | ".join(row) + " |")
        return "\n".join(lines)

    widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows)) if rows else len(headers[i])
        for i in range(len(headers))
    ]
    lines = ["  ".join(headers[i].ljust(widths[i]) for i in range(len(headers)))]
    for row in rows:
        lines.append("  ".join(row[i].ljust(widths[i]) for i in range(len(headers))))
    return "\n".join(lines)
