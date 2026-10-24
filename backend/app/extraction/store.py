from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Engine, and_, func, or_, text
from sqlmodel import Session, select

from app.extraction.models import Extraction, ExtractionEvent
from app.extraction.schemas import LinkedEvent, PostInput
from app.fpl.models.reference import Fixture, Player
from app.tweets.classes import source_post_sql
from app.tweets.models import Tweet

RETRY_AFTER = timedelta(minutes=10)
# 5 passes of up to 3 model calls: a post that still fails is left failed until
# `reextract --failed` is run by hand.
MAX_EXTRACTIONS = 5
# A confirmed line-up this long before the club's next Premier League kickoff is about another
# match (a European or cup tie, or one already played): the managers' own team news comes up to
# about 54 hours before kickoff, the stray line-ups from 67 hours (GW6-GW8 data, BACKLOG #54).
LINEUP_MAX_LEAD = timedelta(hours=60)
LINEUP_EVENTS = ("confirmed_starter", "benched")


@dataclass(frozen=True)
class ExtractionRecord:
    tweet_x_id: int
    status: str  # extracted | failed
    provider: str
    model: str
    prompt_version: str
    started_at: datetime
    finished_at: datetime
    attempts: int
    error_class: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    latency_seconds: float | None = None


@dataclass(frozen=True)
class EventRecord:
    mention: str
    team_mention: str | None
    player_season: str | None
    player_fpl_id: int | None
    event_type: str
    certainty: str
    player_web_name: str | None = None


@dataclass(frozen=True)
class CurrentExtraction:
    extraction_id: int
    tweet_x_id: int
    author_handle: str
    is_repost: bool
    is_reply: bool
    provider: str
    model: str
    prompt_version: str
    started_at: datetime
    finished_at: datetime
    events: list[EventRecord]
    created_at: datetime
    text: str
    reposted_author_handle: str | None = None


@dataclass(frozen=True)
class LatestExtraction:
    tweet_x_id: int
    status: str
    finished_at: datetime
    latency_seconds: float | None


@dataclass(frozen=True)
class ExtractionStatus:
    waiting: int
    failed_posts: int  # given up by the loop; `reextract --failed` takes them
    retrying: int  # failed so far; the loop takes them back after RETRY_AFTER
    latest: LatestExtraction | None


def is_stale_lineup(
    event_type: str, certainty: str, created_at: datetime, next_kickoff: datetime | None
) -> bool:
    return (
        event_type in LINEUP_EVENTS
        and certainty == "confirmed"
        and next_kickoff is not None
        and next_kickoff - created_at > LINEUP_MAX_LEAD
    )


def _latest_per_post(columns: str, where: str = "") -> str:
    return (
        f"SELECT DISTINCT ON (tweet_x_id) {columns} FROM extraction {where}"
        " ORDER BY tweet_x_id, finished_at DESC, id DESC"
    )


def _post_from_tweet(tweet: Tweet) -> PostInput:
    return PostInput(
        x_id=tweet.x_id,
        author_handle=tweet.author_handle,
        text=tweet.text,
        created_at=tweet.created_at,
        is_repost=tweet.is_repost,
        is_reply=tweet.is_reply,
    )


def save_extraction(session: Session, record: ExtractionRecord, events: list[LinkedEvent]) -> int:
    row = Extraction(
        tweet_x_id=record.tweet_x_id,
        status=record.status,
        provider=record.provider,
        model=record.model,
        prompt_version=record.prompt_version,
        started_at=record.started_at,
        finished_at=record.finished_at,
        attempts=record.attempts,
        error_class=record.error_class,
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        cost_usd=record.cost_usd,
        latency_seconds=record.latency_seconds,
    )
    session.add(row)
    session.flush()
    for event in events:
        session.add(
            ExtractionEvent(
                extraction_id=row.id,
                mention=event.mention,
                team_mention=event.team,
                player_season=event.player_season,
                player_fpl_id=event.player_fpl_id,
                event_type=event.event_type,
                certainty=event.certainty,
            )
        )
    session.commit()
    return row.id


def next_pending(
    session: Session,
    now: datetime,
    retry_after: timedelta = RETRY_AFTER,
    max_extractions: int = MAX_EXTRACTIONS,
) -> PostInput | None:
    history = (
        select(
            Extraction.tweet_x_id,
            func.count().label("extractions"),
            func.bool_and(Extraction.status == "failed").label("never_extracted"),
            func.max(Extraction.finished_at).label("last_finished_at"),
        )
        .group_by(Extraction.tweet_x_id)
        .subquery()
    )
    # Never-attempted posts go first and newest first, so neither old failures nor a catch-up
    # backlog delay a fresh leak; a post that never succeeded comes back after `retry_after`.
    tweet = session.exec(
        select(Tweet)
        .outerjoin(history, history.c.tweet_x_id == Tweet.x_id)
        .where(
            or_(
                history.c.tweet_x_id.is_(None),
                history.c.never_extracted
                & (history.c.extractions < max_extractions)
                & (history.c.last_finished_at <= now - retry_after),
            )
        )
        .where(text(source_post_sql("tweet")))
        .order_by(history.c.tweet_x_id.is_not(None), Tweet.created_at.desc(), Tweet.x_id.desc())
        .limit(1)
    ).first()
    return _post_from_tweet(tweet) if tweet is not None else None


def posts_for_reextract(
    session: Session,
    x_id: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    failed: bool = False,
) -> list[PostInput]:
    if x_id is not None:
        tweet = session.get(Tweet, x_id)
        return [_post_from_tweet(tweet)] if tweet is not None else []

    if since is not None or until is not None:
        stmt = select(Tweet).order_by(Tweet.created_at, Tweet.x_id)
        if since is not None:
            stmt = stmt.where(Tweet.created_at >= since)
        if until is not None:
            stmt = stmt.where(Tweet.created_at <= until)
        return [_post_from_tweet(t) for t in session.exec(stmt).all()]

    if failed:
        rows = session.execute(
            text(
                "SELECT t.* FROM tweet t JOIN ("
                + _latest_per_post("tweet_x_id, status")
                + ") latest ON latest.tweet_x_id = t.x_id"
                " WHERE latest.status = 'failed'"
                " ORDER BY t.created_at, t.x_id"
            )
        ).mappings()
        return [_post_from_tweet(Tweet(**dict(row))) for row in rows]

    return []


def current_extractions(
    session: Session,
    *,
    x_ids: Sequence[int] | None = None,
    created_from: datetime | None = None,
    created_until: datetime | None = None,
    player: tuple[str, int] | None = None,
    sources_only: bool = False,
    fresh_lineups_only: bool = False,
) -> list[CurrentExtraction]:
    if x_ids is not None and not x_ids:
        return []
    latest_where = "WHERE status = 'extracted'"
    window: list[str] = []
    conditions: list[str] = []
    params: dict[str, object] = {}
    if x_ids is not None:
        latest_where += " AND tweet_x_id = ANY(:ids)"
        params["ids"] = list(x_ids)
    if created_from is not None:
        window.append("created_at >= :created_from")
        params["created_from"] = created_from
    if created_until is not None:
        window.append("created_at < :created_until")
        params["created_until"] = created_until
    if window:
        # The window narrows the posts before DISTINCT ON, not after it.
        latest_where += f" AND tweet_x_id IN (SELECT x_id FROM tweet WHERE {' AND '.join(window)})"
    if player is not None:
        # After DISTINCT ON: the current extraction itself must name the player.
        conditions.append(
            "EXISTS (SELECT 1 FROM extraction_event ev WHERE ev.extraction_id = l.id"
            " AND ev.player_season = :player_season AND ev.player_fpl_id = :player_fpl_id)"
        )
        params["player_season"], params["player_fpl_id"] = player
    if sources_only:
        conditions.append(source_post_sql("t"))
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = (
        session.execute(
            text(
                "WITH latest AS ("
                + _latest_per_post("id, tweet_x_id", latest_where)
                + ") SELECT e.id, e.tweet_x_id, e.provider, e.model, e.prompt_version,"
                " e.started_at, e.finished_at, t.author_handle, t.is_repost, t.is_reply,"
                " t.text, t.created_at, t.reposted_author_handle"
                " FROM latest l JOIN extraction e ON e.id = l.id"
                " JOIN tweet t ON t.x_id = l.tweet_x_id " + where + " ORDER BY t.created_at, t.x_id"
            ),
            params,
        )
        .mappings()
        .all()
    )
    if not rows:
        return []
    events: dict[int, list[EventRecord]] = {row["id"]: [] for row in rows}
    stmt = select(ExtractionEvent, Player.web_name).join(
        Player,
        and_(
            Player.season == ExtractionEvent.player_season,
            Player.fpl_id == ExtractionEvent.player_fpl_id,
        ),
        isouter=True,
    )
    if fresh_lineups_only:
        next_kickoff = (
            select(func.min(Fixture.kickoff_at))
            .where(
                Fixture.season == Player.season,
                or_(
                    Fixture.team_h_fpl_id == Player.team_fpl_id,
                    Fixture.team_a_fpl_id == Player.team_fpl_id,
                ),
                Fixture.kickoff_at > Tweet.created_at,
            )
            .correlate(Player, Tweet)
            .scalar_subquery()
        )
        stmt = (
            stmt.add_columns(Tweet.created_at, next_kickoff)
            .join(Extraction, Extraction.id == ExtractionEvent.extraction_id)
            .join(Tweet, Tweet.x_id == Extraction.tweet_x_id)
        )
    event_rows = session.exec(
        stmt.where(ExtractionEvent.extraction_id.in_(list(events))).order_by(ExtractionEvent.id)
    ).all()
    for event, web_name, *lineup_check in event_rows:
        if lineup_check and is_stale_lineup(event.event_type, event.certainty, *lineup_check):
            continue
        events[event.extraction_id].append(
            EventRecord(
                mention=event.mention,
                team_mention=event.team_mention,
                player_season=event.player_season,
                player_fpl_id=event.player_fpl_id,
                event_type=event.event_type,
                certainty=event.certainty,
                player_web_name=web_name,
            )
        )
    return [
        CurrentExtraction(
            extraction_id=row["id"],
            tweet_x_id=row["tweet_x_id"],
            author_handle=row["author_handle"],
            is_repost=row["is_repost"],
            is_reply=row["is_reply"],
            provider=row["provider"],
            model=row["model"],
            prompt_version=row["prompt_version"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            events=events[row["id"]],
            created_at=row["created_at"],
            text=row["text"],
            reposted_author_handle=row["reposted_author_handle"],
        )
        for row in rows
    ]


def extraction_status(engine: Engine) -> ExtractionStatus:
    with engine.connect() as conn:
        waiting = conn.execute(
            text(
                "SELECT count(*) FROM tweet t"
                " WHERE NOT EXISTS (SELECT 1 FROM extraction e WHERE e.tweet_x_id = t.x_id)"
                f" AND {source_post_sql('t')}"
            )
        ).scalar_one()
        failed_posts = conn.execute(
            text(
                "SELECT count(*) FROM ("
                + _latest_per_post("tweet_x_id, status")
                + ") latest WHERE latest.status = 'failed'"
            )
        ).scalar_one()
        retrying = conn.execute(
            text(
                "SELECT count(*) FROM tweet t JOIN (SELECT tweet_x_id FROM extraction"
                " GROUP BY tweet_x_id HAVING bool_and(status = 'failed') AND count(*) < :max)"
                f" history ON history.tweet_x_id = t.x_id WHERE {source_post_sql('t')}"
            ),
            {"max": MAX_EXTRACTIONS},
        ).scalar_one()
        latest_row = (
            conn.execute(
                text(
                    "SELECT tweet_x_id, status, finished_at, latency_seconds FROM extraction"
                    " ORDER BY finished_at DESC, id DESC LIMIT 1"
                )
            )
            .mappings()
            .first()
        )
    latest = (
        LatestExtraction(
            tweet_x_id=latest_row["tweet_x_id"],
            status=latest_row["status"],
            finished_at=latest_row["finished_at"],
            latency_seconds=latest_row["latency_seconds"],
        )
        if latest_row is not None
        else None
    )
    return ExtractionStatus(
        waiting=waiting, failed_posts=failed_posts - retrying, retrying=retrying, latest=latest
    )
