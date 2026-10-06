import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import Engine
from sqlmodel import Session, select

from app.alerts.config import AlertConfig
from app.alerts.players import listed_players
from app.alerts.render import render_alert
from app.alerts.schemas import AlertDeadline, ListedPlayer, PlayerReport
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
from app.corroboration.schemas import Corroboration
from app.corroboration.service import CorroborationRuntime, corroborate
from app.corroboration.sources import window_start
from app.delivery.channels.base import Message
from app.delivery.service import DeliveryService
from app.fpl.models.reference import Season

logger = logging.getLogger(__name__)

SEARCH_FAILED_REASON = "the search part failed"


@dataclass(frozen=True)
class AlertsRuntime:
    config: AlertConfig
    league_ids: list[int]
    delivery: DeliveryService
    corroboration: CorroborationRuntime


@dataclass(frozen=True)
class SlotDraft:
    kind: Literal["digest", "news"]
    as_of: datetime
    message: Message | None
    reports: list[PlayerReport]
    posts: list[IncludedPost]

    @property
    def skipped(self) -> bool:
        return self.message is None


def alert_key(deadline: AlertDeadline, kind: str, suffix: int | str) -> str:
    return f"alert:{deadline.key}:{kind}:{suffix}"


def current_season(session: Session) -> str | None:
    return session.exec(select(Season.label).order_by(Season.label.desc())).first()


def alert_window_start(
    session: Session, as_of: datetime, deadline: AlertDeadline, config: AlertConfig
) -> datetime:
    """The previous deadline, but no further back than the configured lookback before the alert
    deadline: a long break between deadlines (an international break) would otherwise fill
    alerts with stale news. Counting from the deadline keeps one window for all its alerts."""
    return max(window_start(session, as_of), deadline.deadline_at - config.max_lookback)


def shown_x_ids(result: Corroboration) -> set[int]:
    shown = {c.x_id for c in result.supporting + result.contradicting}
    if result.anchor is not None:
        shown.add(result.anchor.post.x_id)
    return shown


def _is_new(origins: set[int], included: set[int]) -> bool:
    return not origins & included


def corroborate_player(
    engine: Engine,
    runtime: CorroborationRuntime,
    listed: ListedPlayer,
    as_of: datetime,
    since: datetime,
    new_since: datetime | None,
    included: set[int],
    origins: dict[int, set[int]],
) -> PlayerReport:
    search_failed = False
    try:
        result = corroborate(engine, listed.player, as_of, new_since, since=since, runtime=runtime)
    except Exception as exc:
        logger.warning("alert corroboration failed: %s", type(exc).__name__)
        search_failed = True
        result = corroborate(
            engine,
            listed.player,
            as_of,
            new_since,
            since=since,
            runtime=sql_only_runtime(SEARCH_FAILED_REASON, runtime.tracer),
        )
    shown = shown_x_ids(result)
    missing = [x_id for x_id in shown if x_id not in origins]
    if missing:
        with Session(engine) as session:
            origins = {**origins, **post_origin_sets(session, missing)}
    new_x_ids = frozenset(
        x_id
        for x_id in {*listed.claim_x_ids, *shown}
        if _is_new(origins.get(x_id, {x_id}), included)
    )
    return PlayerReport(listed, result, new_x_ids, search_failed)


def included_rows(reports: list[PlayerReport]) -> list[IncludedPost]:
    rows = []
    for report in reports:
        player = report.listed.player
        shown = {
            c.x_id for c in report.corroboration.supporting + report.corroboration.contradicting
        }
        for x_id in sorted({*report.listed.claim_x_ids, *shown}):
            freshness = "new" if x_id in report.new_x_ids else "context"
            rows.append(IncludedPost(player.season, player.fpl_id, x_id, freshness))
    return rows


def build_slot_alert(
    engine: Engine,
    runtime: AlertsRuntime,
    deadline: AlertDeadline,
    kind: Literal["digest", "news"],
    slot_minutes: int,
    as_of: datetime,
    included: set[int],
    new_since: datetime | None = None,
) -> SlotDraft:
    with Session(engine) as session:
        season = current_season(session)
        if season is None:
            return SlotDraft(kind, as_of, None, [], [])
        start = alert_window_start(session, as_of, deadline, runtime.config)
        listed = listed_players(session, season, runtime.league_ids, start, as_of, runtime.config)
        all_claims = sorted({x for item in listed for x in item.claim_x_ids})
        origins = post_origin_sets(session, all_claims)

    if kind == "news":
        listed = [
            item
            for item in listed
            if any(_is_new(origins.get(x, {x}), included) for x in item.claim_x_ids)
        ]
        if not listed:
            return SlotDraft(kind, as_of, None, [], [])

    reports = [
        corroborate_player(
            engine, runtime.corroboration, item, as_of, start, new_since, included, origins
        )
        for item in listed
    ]
    message = render_alert(kind, deadline, as_of, reports)
    return SlotDraft(kind, as_of, message, reports, included_rows(reports))


def run_slot(
    engine: Engine,
    runtime: AlertsRuntime,
    deadline: AlertDeadline,
    slot_index: int,
    clock: Clock,
) -> str:
    slot = runtime.config.slots[slot_index]
    kind: Literal["digest", "news"] = "digest" if slot_index == 0 else "news"
    key = alert_key(deadline, kind, slot)
    as_of = clock.now()
    if as_of >= deadline.deadline_at:
        logger.info("alert %s not built: the deadline has passed", key)
        return "cutoff"
    with Session(engine) as session:
        if alert_exists(session, key):
            return "exists"
        included = included_origins(session, deadline.key)
        previous = last_alert_as_of(session, deadline.key)

    draft = build_slot_alert(
        engine, runtime, deadline, kind, slot, as_of, included, new_since=previous
    )
    if draft.message is None:
        record_alert(
            engine,
            key=key,
            deadline=deadline,
            kind=kind,
            slot_minutes=slot,
            trigger_x_id=None,
            as_of=as_of,
            status="skipped",
            delivery_log_id=None,
            posts=[],
            recorded_at=clock.now(),
        )
        logger.info("alert %s skipped: nothing new", key)
        return "skipped"

    if clock.now() >= deadline.deadline_at:
        logger.info("alert %s not sent: the deadline has passed", key)
        return "cutoff"
    outcome = runtime.delivery.send(key, "alert", draft.message)
    if outcome.status == "disabled":
        return "disabled"
    status = "failed" if outcome.status == "failed" else "sent"
    record_alert(
        engine,
        key=key,
        deadline=deadline,
        kind=kind,
        slot_minutes=slot,
        trigger_x_id=None,
        as_of=as_of,
        status=status,
        delivery_log_id=outcome.log_id,
        posts=draft.posts,
        recorded_at=clock.now(),
    )
    logger.info(
        "alert %s %s: players=%d posts=%d",
        key,
        status,
        len(draft.reports),
        len(draft.posts),
    )
    return status
