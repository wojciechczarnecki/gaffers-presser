import math
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlmodel import Session

LEGS = ("post -> first fetch", "fetch -> extraction done", "extraction -> accepted", "total")


@dataclass(frozen=True)
class PostLatency:
    x_id: int
    created_at: datetime
    first_fetched_at: datetime
    extracted_at: datetime
    accepted_at: datetime

    def legs(self) -> tuple[float, float, float, float]:
        return (
            (self.first_fetched_at - self.created_at).total_seconds(),
            (self.extracted_at - self.first_fetched_at).total_seconds(),
            (self.accepted_at - self.extracted_at).total_seconds(),
            (self.accepted_at - self.created_at).total_seconds(),
        )


@dataclass(frozen=True)
class LegSummary:
    p50: float
    p95: float
    maximum: float


def percentile(values: list[float], percent: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(percent / 100 * len(ordered)))
    return ordered[rank - 1]


def summarize(latencies: list[PostLatency]) -> list[LegSummary] | None:
    if not latencies:
        return None
    columns = list(zip(*(item.legs() for item in latencies), strict=True))
    return [LegSummary(percentile(list(c), 50), percentile(list(c), 95), max(c)) for c in columns]


def post_latencies(session: Session, deadline_key: str) -> list[PostLatency]:
    rows = session.execute(
        text(
            "SELECT DISTINCT ON (t.x_id) t.x_id, t.created_at, t.first_fetched_at,"
            " (SELECT min(e.finished_at) FROM extraction e"
            "  WHERE e.tweet_x_id = t.x_id AND e.status = 'extracted') AS extracted_at,"
            " d.accepted_at"
            " FROM alert a JOIN alert_post ap ON ap.alert_id = a.id"
            " JOIN tweet t ON t.x_id = ap.tweet_x_id"
            " JOIN delivery_log d ON d.id = a.delivery_log_id"
            " WHERE a.deadline_key = :deadline_key AND a.status = 'sent'"
            " AND ap.freshness = 'new' AND d.accepted_at IS NOT NULL"
            " ORDER BY t.x_id, d.accepted_at"
        ),
        {"deadline_key": deadline_key},
    )
    return [
        PostLatency(
            row.x_id, row.created_at, row.first_fetched_at, row.extracted_at, row.accepted_at
        )
        for row in rows
        if row.extracted_at is not None
    ]


def latest_deadline_key(session: Session, rehearsal: bool = False) -> str | None:
    query = "SELECT deadline_key FROM alert"
    if rehearsal:
        query += " WHERE deadline_key LIKE 'rehearsal:%'"
    query += " ORDER BY recorded_at DESC, id DESC LIMIT 1"
    return session.execute(text(query)).scalar_one_or_none()


def has_alerts(session: Session, deadline_key: str) -> bool:
    row = session.execute(
        text("SELECT 1 FROM alert WHERE deadline_key = :deadline_key LIMIT 1"),
        {"deadline_key": deadline_key},
    ).first()
    return row is not None


def format_report(deadline_key: str, latencies: list[PostLatency]) -> list[str]:
    lines = [f"Alert deadline: {deadline_key}", f"Posts: {len(latencies)}"]
    summary = summarize(latencies)

    def cell(value: float | None) -> str:
        return "-" if value is None else f"{value:.1f} s"

    lines.append(f"{'':26}{'p50':>10}{'p95':>10}{'max':>10}")
    for index, leg in enumerate(LEGS):
        stats = summary[index] if summary else None
        lines.append(
            f"{leg:26}{cell(stats.p50 if stats else None):>10}"
            f"{cell(stats.p95 if stats else None):>10}{cell(stats.maximum if stats else None):>10}"
        )
    return lines
