import logging
import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import Engine, func
from sqlmodel import Session, select

from app.corroboration.evaluation.cases import (
    LABELS,
    CaseAnchor,
    CasePlayer,
    CasePost,
    JudgeCase,
    is_known_miss,
)
from app.corroboration.judge import Judge, JudgeInput
from app.corroboration.schemas import Claim, PlayerRef, PostRef
from app.corroboration.sources import retrieval_candidates
from app.extraction.linking import load_players
from app.extraction.store import current_extractions
from app.llm.pricing import Price
from app.retrieval.embedder import Embedder
from app.tweets.models import Tweet

logger = logging.getLogger(__name__)

TARGET_SIZE = 60
DEV_SHARE = 0.3
UNRELATED_CAP = 0.4
SQL_CONTEXT_PER_ANCHOR = 3
ANCHORS_PER_PLAYER = 2


@dataclass(frozen=True)
class Candidate:
    player: PlayerRef
    anchor: Claim
    post: PostRef
    has_player_event: bool


def _claims_by_player(engine: Engine) -> dict[PlayerRef, list[Claim]]:
    with Session(engine) as session:
        players, teams = load_players(session)
        rows = current_extractions(session)
    team_names = {team.fpl_id: team.name for team in teams}
    refs = {
        (p.season, p.fpl_id): PlayerRef(
            p.season, p.fpl_id, p.web_name, team_names.get(p.team_fpl_id)
        )
        for p in players
    }
    claims: dict[PlayerRef, list[Claim]] = defaultdict(list)
    for row in rows:
        post = PostRef(
            row.tweet_x_id,
            row.author_handle,
            row.reposted_author_handle,
            row.is_repost,
            row.created_at,
            row.text,
        )
        seen: set[tuple[str, int]] = set()
        for event in row.events:
            key = (event.player_season, event.player_fpl_id)
            if key[0] is None or key not in refs or key in seen:
                continue
            seen.add(key)
            claims[refs[key]].append(Claim(post, event.event_type, event.certainty))
    return claims


def _anchors(claims: Sequence[Claim]) -> list[Claim]:
    ordered = sorted(claims, key=lambda c: (c.post.created_at, c.post.x_id), reverse=True)
    anchors = [ordered[0]]
    other = next((c for c in ordered if c.event_type != ordered[0].event_type), None)
    if other is not None and ANCHORS_PER_PLAYER > 1:
        anchors.append(other)
    return anchors


def build_candidates(
    engine: Engine, embedder: Embedder, prices: dict[str, Price] | None = None
) -> list[Candidate]:
    with Session(engine) as session:
        first, last = session.exec(
            select(func.min(Tweet.created_at), func.max(Tweet.created_at))
        ).one()
    if first is None or last is None:
        return []
    as_of = last + timedelta(minutes=1)
    candidates: list[Candidate] = []
    for player, claims in sorted(_claims_by_player(engine).items(), key=lambda i: i[0].fpl_id):
        claimed = {c.post.x_id for c in claims}
        for anchor in _anchors(claims):
            found = retrieval_candidates(
                engine, player, first, as_of, embedder=embedder, claimed_ids=claimed, prices=prices
            )
            for post in found.posts:
                candidates.append(Candidate(player, anchor, post, has_player_event=False))
            context = [c for c in claims if c.post.x_id != anchor.post.x_id]
            context.sort(key=lambda c: (c.post.created_at, c.post.x_id), reverse=True)
            for claim in context[:SQL_CONTEXT_PER_ANCHOR]:
                candidates.append(Candidate(player, anchor, claim.post, has_player_event=True))
    return candidates


@dataclass(frozen=True)
class Prelabelled:
    cases: list[JudgeCase]
    cost_usd: float | None
    failed: int


@dataclass(frozen=True)
class Built:
    cases: list[JudgeCase]
    candidates: int
    prelabelled: int
    failed: int
    cost_usd: float | None


def prelabel(candidates: Sequence[Candidate], judge: Judge, model: str) -> Prelabelled:
    cases: list[JudgeCase] = []
    costs: list[float] = []
    failed = 0
    for candidate in candidates:
        try:
            reply = judge.run(JudgeInput(candidate.player, candidate.anchor, candidate.post))
        except Exception as exc:
            logger.warning("a candidate was not pre-labelled: %s", type(exc).__name__)
            failed += 1
            continue
        if reply.cost_usd is not None:
            costs.append(reply.cost_usd)
        anchor, post, player = candidate.anchor, candidate.post, candidate.player
        cases.append(
            JudgeCase(
                id=f"jc-{player.fpl_id}-{anchor.post.x_id}-{post.x_id}",
                split="test",
                player=CasePlayer(
                    fpl_id=player.fpl_id, web_name=player.web_name, team=player.team_name
                ),
                anchor=CaseAnchor(
                    x_id=anchor.post.x_id,
                    author_handle=anchor.post.author_handle,
                    created_at=anchor.post.created_at,
                    event_type=anchor.event_type,
                    certainty=anchor.certainty,
                    text=anchor.post.text,
                ),
                post=CasePost(
                    x_id=post.x_id,
                    author_handle=post.author_handle,
                    reposted_author_handle=post.reposted_author_handle,
                    is_repost=post.is_repost,
                    created_at=post.created_at,
                    text=post.text,
                ),
                expected=reply.parsed.label,
                labelled_by=model,
                reviewed=False,
                has_player_event=candidate.has_player_event,
            )
        )
    return Prelabelled(cases, sum(costs) if costs else None, failed)


def select_cases(
    cases: Sequence[JudgeCase],
    size: int = TARGET_SIZE,
    seed: int = 7,
    unrelated_cap: float = UNRELATED_CAP,
) -> list[JudgeCase]:
    rng = random.Random(seed)
    buckets: dict[str, list[JudgeCase]] = {label: [] for label in LABELS}
    for case in sorted(cases, key=lambda c: c.id):
        buckets[case.expected].append(case)
    for bucket in buckets.values():
        rng.shuffle(bucket)
        # Known misses first: they are the reason the judge exists.
        bucket.sort(key=lambda c: not is_known_miss(c))

    unrelated_limit = int(size * unrelated_cap)
    limits = {label: size // len(LABELS) for label in LABELS}
    chosen: dict[str, list[JudgeCase]] = {
        label: bucket[: limits[label]] for label, bucket in buckets.items()
    }
    total = sum(len(v) for v in chosen.values())
    order = [label for label in LABELS if label != "unrelated"] + ["unrelated"]
    while total < size:
        progressed = False
        for label in order:
            if total >= size:
                break
            taken = len(chosen[label])
            if taken >= len(buckets[label]):
                continue
            if label == "unrelated" and taken >= unrelated_limit:
                continue
            chosen[label].append(buckets[label][taken])
            total += 1
            progressed = True
        if not progressed:
            break
    return sorted((c for v in chosen.values() for c in v), key=lambda c: c.id)


def assign_split(
    cases: Sequence[JudgeCase], dev_share: float = DEV_SHARE, seed: int = 7
) -> list[JudgeCase]:
    rng = random.Random(seed)
    by_label: dict[str, list[JudgeCase]] = defaultdict(list)
    for case in sorted(cases, key=lambda c: c.id):
        by_label[case.expected].append(case)
    dev_ids: set[str] = set()
    for label in LABELS:
        bucket = by_label[label]
        rng.shuffle(bucket)
        dev_count = min(round(dev_share * len(bucket)), max(len(bucket) - 1, 0))
        dev_ids.update(case.id for case in bucket[:dev_count])
    return [
        case.model_copy(update={"split": "dev" if case.id in dev_ids else "test"})
        for case in sorted(cases, key=lambda c: c.id)
    ]


def build_cases(
    engine: Engine,
    embedder: Embedder,
    judge: Judge,
    *,
    size: int = TARGET_SIZE,
    seed: int = 7,
    prices: dict[str, Price] | None = None,
) -> Built:
    candidates = build_candidates(engine, embedder, prices)
    labelled = prelabel(candidates, judge, judge.model)
    cases = assign_split(select_cases(labelled.cases, size, seed), DEV_SHARE, seed)
    return Built(cases, len(candidates), len(labelled.cases), labelled.failed, labelled.cost_usd)
