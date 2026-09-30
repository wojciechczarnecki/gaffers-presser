from dataclasses import dataclass
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Engine, text
from sqlmodel import Session, select

from app.extraction.models import Extraction, ExtractionEvent
from app.extraction.schemas import LinkedEvent, PostInput
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


def current_extractions(
    session: Session,
    *,
    x_ids: Sequence[int] | None = None,
    created_from: datetime | None = None,
    created_until: datetime | None = None,
) -> list[CurrentExtraction]:
    return []


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
                " SELECT DISTINCT ON (tweet_x_id) tweet_x_id, status FROM extraction"
                " ORDER BY tweet_x_id, finished_at DESC, id DESC"
                ") latest ON latest.tweet_x_id = t.x_id"
                " WHERE latest.status = 'failed'"
                " ORDER BY t.created_at, t.x_id"
            )
        ).mappings()
        return [_post_from_tweet(Tweet(**dict(row))) for row in rows]

    return []


def current_extraction(session: Session, x_id: int) -> CurrentExtraction | None:
    row = session.exec(
        select(Extraction, Tweet.author_handle, Tweet.is_repost, Tweet.is_reply)
        .join(Tweet, Tweet.x_id == Extraction.tweet_x_id)
        .where(Extraction.tweet_x_id == x_id, Extraction.status == "extracted")
        .order_by(Extraction.finished_at.desc(), Extraction.id.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    extraction, author_handle, is_repost, is_reply = row
    events = session.exec(
        select(ExtractionEvent)
        .where(ExtractionEvent.extraction_id == extraction.id)
        .order_by(ExtractionEvent.id)
    ).all()
    return CurrentExtraction(
        extraction_id=extraction.id,
        tweet_x_id=x_id,
        author_handle=author_handle,
        is_repost=is_repost,
        is_reply=is_reply,
        provider=extraction.provider,
        model=extraction.model,
        prompt_version=extraction.prompt_version,
        started_at=extraction.started_at,
        finished_at=extraction.finished_at,
        events=[
            EventRecord(
                mention=e.mention,
                team_mention=e.team_mention,
                player_season=e.player_season,
                player_fpl_id=e.player_fpl_id,
                event_type=e.event_type,
                certainty=e.certainty,
            )
            for e in events
        ],
    )


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
                " SELECT DISTINCT ON (tweet_x_id) tweet_x_id, status FROM extraction"
                " ORDER BY tweet_x_id, finished_at DESC, id DESC"
                ") latest WHERE latest.status = 'failed'"
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
