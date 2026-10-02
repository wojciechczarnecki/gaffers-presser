import logging
from datetime import datetime

from sqlalchemy import Engine, text
from sqlmodel import Session

from app.alerts.players import listed_players
from app.alerts.render import render_alert
from app.alerts.schemas import AlertDeadline, ListedPlayer, PlayerReport
from app.alerts.service import (
    AlertsRuntime,
    alert_key,
    current_season,
    shown_x_ids,
)
from app.alerts.store import (
    IncludedPost,
    alert_exists,
    included_origins,
    last_alert_as_of,
    post_origin_sets,
    record_alert,
)
from app.core.clock import Clock
from app.corroboration.runtime import sql_only_runtime
from app.corroboration.service import corroborate
from app.corroboration.sources import window_start
from app.extraction.store import CurrentExtraction, current_extractions

logger = logging.getLogger(__name__)

BREAKING_SKIPPED_REASON = "breaking alerts use the SQL rules only"


_EXTRACTED_FROM = (
    "SELECT 1 FROM extraction WHERE status = 'extracted' AND finished_at >= :since LIMIT 1"
)
_EXTRACTED_AFTER = (
    "SELECT 1 FROM extraction WHERE status = 'extracted' AND finished_at > :since LIMIT 1"
)


def _extracted_since(session: Session, since: datetime, *, strictly_after: bool) -> bool:
    query = _EXTRACTED_AFTER if strictly_after else _EXTRACTED_FROM
    return session.execute(text(query), {"since": since}).first() is not None


def newest_extraction(session: Session) -> datetime | None:
    return session.execute(
        text("SELECT max(finished_at) FROM extraction WHERE status = 'extracted'")
    ).scalar_one_or_none()


def _candidates(
    session: Session,
    season: str,
    start: datetime,
    now: datetime,
    since_extracted: datetime,
    listed: dict[int, ListedPlayer],
) -> list[tuple[CurrentExtraction, list[ListedPlayer]]]:
    found = []
    for row in current_extractions(session, created_from=start, created_until=now):
        if row.finished_at < since_extracted:
            continue
        named = sorted(
            {
                event.player_fpl_id
                for event in row.events
                if event.player_season == season and event.player_fpl_id in listed
            }
        )
        if named:
            found.append((row, [listed[fpl_id] for fpl_id in named]))
    return found


def _breaking_rows(reports: list[PlayerReport], trigger: int) -> list[IncludedPost]:
    rows = []
    for report in reports:
        player = report.listed.player
        for x_id in sorted({trigger, *shown_x_ids(report.corroboration)}):
            freshness = "new" if x_id == trigger else "context"
            rows.append(IncludedPost(player.season, player.fpl_id, x_id, freshness))
    return rows


def run_breaking(
    engine: Engine,
    runtime: AlertsRuntime,
    deadline: AlertDeadline,
    since_extracted: datetime,
    clock: Clock,
    *,
    processed_until: datetime | None = None,
) -> int:
    now = clock.now()
    if now >= deadline.deadline_at:
        return 0
    with Session(engine) as session:
        if processed_until is not None and processed_until >= since_extracted:
            gate = _extracted_since(session, processed_until, strictly_after=True)
        else:
            gate = _extracted_since(session, since_extracted, strictly_after=False)
        if not gate:
            return 0
        season = current_season(session)
        if season is None:
            return 0
        start = window_start(session, now)
        players, _ = listed_players(session, season, runtime.league_ids, start, now, runtime.config)
        listed = {item.player.fpl_id: item for item in players}
        candidates = _candidates(session, season, start, now, since_extracted, listed)
        included = included_origins(session, deadline.key)
        previous = last_alert_as_of(session, deadline.key)
        origins = post_origin_sets(session, [row.tweet_x_id for row, _ in candidates])

    sent = 0
    sql_only = sql_only_runtime(BREAKING_SKIPPED_REASON, runtime.corroboration.tracer)
    for row, named in sorted(candidates, key=lambda c: (c[0].created_at, c[0].tweet_x_id)):
        x_id = row.tweet_x_id
        post_origins = origins.get(x_id, {x_id})
        key = alert_key(deadline, "breaking", x_id)
        if post_origins & included:
            continue
        with Session(engine) as session:
            if alert_exists(session, key):
                continue
        as_of = clock.now()
        if as_of >= deadline.deadline_at:
            logger.info("breaking alerts stopped: the deadline has passed")
            break
        reports = []
        for item in named:
            result = corroborate(
                engine,
                item.player,
                as_of,
                previous,
                anchor_x_id=x_id,
                runtime=sql_only,
            )
            reports.append(PlayerReport(item, result, frozenset({x_id}), False))
        message = render_alert("breaking", deadline, as_of, reports, 0)
        if clock.now() >= deadline.deadline_at:
            logger.info("breaking alert %s not sent: the deadline has passed", key)
            break
        outcome = runtime.delivery.send(key, "alert", message)
        if outcome.status == "disabled":
            break
        status = "failed" if outcome.status == "failed" else "sent"
        record_alert(
            engine,
            key=key,
            deadline=deadline,
            kind="breaking",
            slot_minutes=None,
            trigger_x_id=x_id,
            as_of=as_of,
            status=status,
            delivery_log_id=outcome.log_id,
            posts=_breaking_rows(reports, x_id),
            recorded_at=clock.now(),
        )
        included |= post_origins
        logger.info("alert %s %s: players=%d", key, status, len(reports))
        if status == "sent":
            sent += 1
    return sent
