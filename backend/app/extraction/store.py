from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, and_, text
from sqlmodel import Session, select

from app.extraction.models import Extraction, ExtractionEvent
from app.extraction.schemas import LinkedEvent, PostInput
from app.fpl.models.reference import Player
from app.tweets.models import Tweet


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


@dataclass(frozen=True)
class LatestExtraction:
    tweet_x_id: int
    status: str
    finished_at: datetime
    latency_seconds: float | None


@dataclass(frozen=True)
class ExtractionStatus:
    waiting: int
    failed_posts: int
    latest: LatestExtraction | None


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


def next_pending(session: Session) -> PostInput | None:
    extracted_ids = select(Extraction.tweet_x_id).distinct()
    tweet = session.exec(
        select(Tweet)
        .where(Tweet.x_id.not_in(extracted_ids))
        .order_by(Tweet.created_at, Tweet.x_id)
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


def current_extraction(session: Session, x_id: int) -> CurrentExtraction | None:
    rows = current_extractions(session, x_ids=[x_id])
    return rows[0] if rows else None


def current_extractions(
    session: Session,
    *,
    x_ids: Sequence[int] | None = None,
    created_from: datetime | None = None,
    created_until: datetime | None = None,
) -> list[CurrentExtraction]:
    if x_ids is not None and not x_ids:
        return []
    latest_where = "WHERE status = 'extracted'"
    conditions: list[str] = []
    params: dict[str, object] = {}
    if x_ids is not None:
        latest_where += " AND tweet_x_id = ANY(:ids)"
        params["ids"] = list(x_ids)
    if created_from is not None:
        conditions.append("t.created_at >= :created_from")
        params["created_from"] = created_from
    if created_until is not None:
        conditions.append("t.created_at < :created_until")
        params["created_until"] = created_until
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = (
        session.execute(
            text(
                "WITH latest AS ("
                + _latest_per_post("id, tweet_x_id", latest_where)
                + ") SELECT e.id, e.tweet_x_id, e.provider, e.model, e.prompt_version,"
                " e.started_at, e.finished_at, t.author_handle, t.is_repost, t.is_reply,"
                " t.text, t.created_at"
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
    event_rows = session.exec(
        select(ExtractionEvent, Player.web_name)
        .join(
            Player,
            and_(
                Player.season == ExtractionEvent.player_season,
                Player.fpl_id == ExtractionEvent.player_fpl_id,
            ),
            isouter=True,
        )
        .where(ExtractionEvent.extraction_id.in_(list(events)))
        .order_by(ExtractionEvent.id)
    ).all()
    for event, web_name in event_rows:
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
        )
        for row in rows
    ]


def extraction_status(engine: Engine) -> ExtractionStatus:
    with engine.connect() as conn:
        waiting = conn.execute(
            text(
                "SELECT count(*) FROM tweet t"
                " WHERE NOT EXISTS (SELECT 1 FROM extraction e WHERE e.tweet_x_id = t.x_id)"
            )
        ).scalar_one()
        failed_posts = conn.execute(
            text(
                "SELECT count(*) FROM ("
                + _latest_per_post("tweet_x_id, status")
                + ") latest WHERE latest.status = 'failed'"
            )
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
    return ExtractionStatus(waiting=waiting, failed_posts=failed_posts, latest=latest)
