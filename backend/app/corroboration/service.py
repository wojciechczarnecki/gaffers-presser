import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Engine
from sqlmodel import Session

from app.corroboration.judge import Judge, JudgeInput, render_input
from app.corroboration.rules import (
    DEFAULT_RULES,
    GradeRules,
    account_of,
    count_accounts,
    freshness,
    grade,
    label_claim,
    newer_contradiction,
    reversal,
)
from app.corroboration.schemas import (
    Citation,
    Claim,
    Corroboration,
    LabelledPost,
    PlayerRef,
    RetrievalReport,
)
from app.corroboration.sources import (
    EMBED_TIMEOUT_SECONDS,
    retrieval_candidates,
    sql_claims,
    window_start,
)
from app.corroboration.tracing import NULL_CORROBORATION_TRACER, CorroborationTracer
from app.llm.pricing import Price
from app.retrieval.embedder import Embedder

logger = logging.getLogger(__name__)

SKIPPED_NOT_CONFIGURED = "retrieval and the judge are not configured"


@dataclass(frozen=True)
class CorroborationRuntime:
    embedder: Embedder | None
    judge: Judge | None
    tracer: CorroborationTracer = NULL_CORROBORATION_TRACER
    prices: dict[str, Price] = field(default_factory=dict)
    embed_timeout_seconds: float = EMBED_TIMEOUT_SECONDS
    rules: GradeRules = DEFAULT_RULES
    skipped_reason: str | None = None


def _citation(item: LabelledPost, new_since: datetime) -> Citation:
    post = item.post
    return Citation(
        x_id=post.x_id,
        url=f"https://x.com/{post.author_handle}/status/{post.x_id}",
        author_handle=post.author_handle,
        reposted_author_handle=post.reposted_author_handle if post.is_repost else None,
        created_at=post.created_at,
        certainty=item.certainty,
        origin=item.origin,
        label=item.label,
        freshness=freshness(post.created_at, new_since),
        event_type=item.event_type,
        text=post.text,
    )


def _newest_first(items: list[LabelledPost]) -> list[LabelledPost]:
    return sorted(items, key=lambda i: (i.post.created_at, i.post.x_id), reverse=True)


def _judge_candidates(
    engine: Engine,
    runtime: CorroborationRuntime,
    player: PlayerRef,
    anchor: Claim,
    start: datetime,
    as_of: datetime,
    claimed_ids: set[int],
) -> tuple[list[LabelledPost], RetrievalReport]:
    assert runtime.embedder is not None and runtime.judge is not None
    tracer = runtime.tracer
    try:
        candidates = retrieval_candidates(
            engine,
            player,
            start,
            as_of,
            embedder=runtime.embedder,
            claimed_ids=claimed_ids,
            tracer=tracer.retrieval(),
            prices=runtime.prices,
            embed_timeout_seconds=runtime.embed_timeout_seconds,
        )
    except Exception as exc:
        logger.error("corroboration retrieval failed: %s", type(exc).__name__)
        return [], RetrievalReport("ran", failure=f"retrieval failed: {type(exc).__name__}")

    judged: list[LabelledPost] = []
    unjudged = 0
    for post in candidates.posts:
        item = JudgeInput(player, anchor, post)
        try:
            reply = runtime.judge.run(item)
        except Exception as exc:
            unjudged += 1
            logger.warning("corroboration judge call failed: %s", type(exc).__name__)
            tracer.generation(
                model=runtime.judge.model,
                input=render_input(item),
                output=None,
                usage=None,
                cost_usd=None,
                error_class=type(exc).__name__,
            )
            continue
        tracer.generation(
            model=reply.answered_model or runtime.judge.model,
            input=render_input(item),
            output=reply.parsed.label,
            usage=reply.usage,
            cost_usd=reply.cost_usd,
        )
        judged.append(LabelledPost(post=post, label=reply.parsed.label, origin="judge"))
    report = RetrievalReport(
        "ran",
        failed_legs=candidates.failed_legs,
        failure=candidates.failure,
        judged=len(judged),
        unjudged=unjudged,
    )
    return judged, report


def corroborate(
    engine: Engine,
    player: PlayerRef,
    as_of: datetime,
    new_since: datetime | None = None,
    *,
    since: datetime | None = None,
    runtime: CorroborationRuntime,
) -> Corroboration:
    with Session(engine) as session:
        start = window_start(session, as_of, since)
        claims = sql_claims(session, player, start, as_of)
    new_since = new_since if new_since is not None else start
    tracer = runtime.tracer
    span_input = {
        "player": {"season": player.season, "fpl_id": player.fpl_id, "name": player.web_name},
        "as_of": as_of.isoformat(),
        "new_since": new_since.isoformat(),
        "window_start": start.isoformat(),
    }
    with tracer.span("corroboration", span_input) as span:
        if not claims:
            result = Corroboration(
                player=player,
                as_of=as_of,
                window_start=start,
                new_since=new_since,
                anchor=None,
                retrieval=RetrievalReport("skipped", "no claim in the window"),
                trace_id=span.trace_id,
            )
            span.update(output={"anchor": None})
            return result

        anchor = max(claims, key=lambda c: (c.post.created_at, c.post.x_id))
        labelled = [
            LabelledPost(
                post=claim.post,
                label=label_claim(anchor.event_type, claim.event_type),
                origin="sql",
                certainty=claim.certainty,
                event_type=claim.event_type,
            )
            for claim in claims
            if claim.post.x_id != anchor.post.x_id
        ]

        if runtime.embedder is None or runtime.judge is None:
            report = RetrievalReport("skipped", runtime.skipped_reason or SKIPPED_NOT_CONFIGURED)
        else:
            judged, report = _judge_candidates(
                engine, runtime, player, anchor, start, as_of, {c.post.x_id for c in claims}
            )
            labelled.extend(judged)

        counts = count_accounts(labelled, anchor.post)
        is_reversal = reversal(counts.contradicting, anchor.post)
        is_newer = newer_contradiction(counts.contradicting, anchor.post)
        assessed = grade(
            anchor.certainty,
            counts.supporting,
            counts.contradicting,
            is_newer,
            new_since,
            runtime.rules,
        )
        result = Corroboration(
            player=player,
            as_of=as_of,
            window_start=start,
            new_since=new_since,
            anchor=anchor,
            supporting=[_citation(i, new_since) for i in _newest_first(counts.supporting)],
            contradicting=[_citation(i, new_since) for i in _newest_first(counts.contradicting)],
            related=[_citation(i, new_since) for i in _newest_first(counts.related)],
            reversal=is_reversal,
            newer_contradiction=is_newer,
            grade=assessed,
            retrieval=report,
            trace_id=span.trace_id,
        )
        span.update(
            output={
                "anchor": {
                    "x_id": anchor.post.x_id,
                    "event_type": anchor.event_type,
                    "certainty": anchor.certainty,
                    "account": account_of(anchor.post),
                },
                "supporting": len(counts.supporting),
                "contradicting": len(counts.contradicting),
                "related": len(counts.related),
                "reversal": is_reversal,
                "newer_contradiction": is_newer,
                "grade": assessed.level,
                "reasons": list(assessed.reasons),
                "judged": report.judged,
                "unjudged": report.unjudged,
            }
        )
        return result
