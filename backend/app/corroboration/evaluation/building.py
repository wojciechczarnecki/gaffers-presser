import logging
import random
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import Engine, func
from sqlmodel import Session, select

from app.core.errors import AppError
from app.corroboration.evaluation.cases import (
    LABELS,
    CandidateCase,
    CaseAnchor,
    CasePlayer,
    CasePost,
    JudgeCase,
    is_known_miss,
    write_candidates,
)
from app.corroboration.evaluation.dataset import (
    MANIFEST_FILE,
    BuildInfo,
    BuildWindow,
    SetManifest,
    load_manifest,
    write_manifest,
)
from app.corroboration.evaluation.scratch import build_costs, scratch_models
from app.corroboration.judge import Judge, JudgeInput
from app.corroboration.rules import label_claim
from app.corroboration.schemas import Claim, PlayerRef, PostRef
from app.corroboration.sources import claimed_post_ids, retrieval_candidates
from app.extraction.evaluation.judging import LINEUP_KEYWORDS
from app.extraction.linking import load_aliases, load_players
from app.extraction.store import current_extractions
from app.llm.pricing import Price
from app.retrieval.embedder import Embedder
from app.retrieval.evaluation.labelling import CostTracer
from app.retrieval.tracing import NULL_TRACER
from app.tweets.models import Tweet

logger = logging.getLogger(__name__)

TARGET_SIZE = 60
DEV_SHARE = 0.3
UNRELATED_CAP = 0.4
SQL_CONTEXT_PER_ANCHOR = 3
ANCHORS_PER_PLAYER = 2
NEW_CASES_SIZE = 55
MIN_NEW_CONTRADICTS = 12
MAX_INPUTS = 250
LIKELY_INPUTS = 150
# The alert slots of a gameweek that judge posts: the digest and the news slot (alerts has
# ALERT_SLOTS_MINUTES; corroboration may not import it).
ALERT_SLOTS = 2
GAMEWEEKS_PER_MONTH = 4
AVAILABILITY_KEYWORDS = re.compile(
    r"\b(?:out|injur\w*|doubt\w*|ruled out|miss(?:es|ing)?|unavailable|suspended|knock"
    r"|fitness test|withdrawn|sidelined|will not play)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Candidate:
    player: PlayerRef
    anchor: Claim
    post: PostRef
    has_player_event: bool


def _claims_by_player(
    engine: Engine,
    start: datetime | None = None,
    until: datetime | None = None,
    sources_only: bool = False,
) -> dict[PlayerRef, list[Claim]]:
    with Session(engine) as session:
        players, teams = load_players(session)
        rows = current_extractions(
            session,
            created_from=start,
            created_until=until,
            sources_only=sources_only,
            fresh_lineups_only=True,
        )
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
    aliases = load_aliases()[0]
    candidates: list[Candidate] = []
    for player, claims in sorted(_claims_by_player(engine).items(), key=lambda i: i[0].fpl_id):
        with Session(engine) as session:
            claimed = claimed_post_ids(session, player, first, as_of)
        for anchor in _anchors(claims):
            found = retrieval_candidates(
                engine,
                player,
                first,
                as_of,
                embedder=embedder,
                claimed_ids=claimed,
                prices=prices,
                aliases=aliases,
            )
            for post in found.posts:
                candidates.append(Candidate(player, anchor, post, has_player_event=False))
            context = [c for c in claims if c.post.x_id != anchor.post.x_id]
            context.sort(key=lambda c: (c.post.created_at, c.post.x_id), reverse=True)
            for claim in context[:SQL_CONTEXT_PER_ANCHOR]:
                candidates.append(Candidate(player, anchor, claim.post, has_player_event=True))
    return candidates


@dataclass(frozen=True)
class WindowCandidates:
    candidates: list[CandidateCase]
    judge_calls_per_month: float
    query_embeddings_cost_usd: float


def _tier_of_retrieved(anchor_type: str, player_has_contradiction: bool, text: str) -> str:
    if player_has_contradiction:
        return "retrieved-likely"
    opposite = LINEUP_KEYWORDS if anchor_type in ("out", "doubt", "benched") else None
    if anchor_type == "confirmed_starter":
        opposite = AVAILABILITY_KEYWORDS
    if opposite is not None and opposite.search(text):
        return "retrieved-likely"
    return "retrieved"


def _candidate(
    player: PlayerRef, anchor: Claim, post: PostRef, has_player_event: bool, tier: str
) -> CandidateCase:
    return CandidateCase(
        id=f"jc-{player.fpl_id}-{anchor.post.x_id}-{post.x_id}",
        player=CasePlayer(fpl_id=player.fpl_id, web_name=player.web_name, team=player.team_name),
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
        has_player_event=has_player_event,
        tier=tier,
    )


def build_window_candidates(
    engine: Engine,
    embedder: Embedder,
    windows: Sequence[BuildWindow],
    prices: dict[str, Price] | None = None,
) -> WindowCandidates:
    aliases = load_aliases()[0]
    tracer = CostTracer(NULL_TRACER)
    candidates: list[CandidateCase] = []
    newest_anchor_posts = 0
    for window in windows:
        claims_by_player = _claims_by_player(engine, window.start, window.end, sources_only=True)
        for player, claims in sorted(claims_by_player.items(), key=lambda i: i[0].fpl_id):
            with Session(engine) as session:
                claimed = claimed_post_ids(session, player, window.start, window.end)
            for position, anchor in enumerate(_anchors(claims)):
                found = retrieval_candidates(
                    engine,
                    player,
                    window.start,
                    window.end,
                    embedder=embedder,
                    claimed_ids=claimed,
                    prices=prices,
                    aliases=aliases,
                    tracer=tracer,
                )
                context = [c for c in claims if c.post.x_id != anchor.post.x_id]
                contradicting = [
                    c
                    for c in context
                    if label_claim(anchor.event_type, c.event_type) == "contradicts"
                ]
                for post in found.posts:
                    tier = _tier_of_retrieved(anchor.event_type, bool(contradicting), post.text)
                    candidates.append(_candidate(player, anchor, post, False, tier))
                if position == 0:
                    newest_anchor_posts += len(found.posts)
                context.sort(
                    key=lambda c: (
                        label_claim(anchor.event_type, c.event_type) != "contradicts",
                        -c.post.created_at.timestamp(),
                        -c.post.x_id,
                    )
                )
                for claim in context[:SQL_CONTEXT_PER_ANCHOR]:
                    is_contradiction = (
                        label_claim(anchor.event_type, claim.event_type) == "contradicts"
                    )
                    candidates.append(
                        _candidate(
                            player,
                            anchor,
                            claim.post,
                            True,
                            "sql-contradicts" if is_contradiction else "sql",
                        )
                    )
    per_month = newest_anchor_posts * ALERT_SLOTS / max(len(windows), 1) * GAMEWEEKS_PER_MONTH
    return WindowCandidates(candidates, per_month, tracer.embedding_cost_usd or 0.0)


def sample_candidates(
    candidates: Sequence[CandidateCase],
    max_inputs: int = MAX_INPUTS,
    seed: int = 7,
    likely_inputs: int = LIKELY_INPUTS,
) -> list[CandidateCase]:
    rng = random.Random(seed)
    by_tier: dict[str, list[CandidateCase]] = defaultdict(list)
    for candidate in sorted(candidates, key=lambda c: c.id):
        by_tier[candidate.tier].append(candidate)
    for tier in by_tier.values():
        rng.shuffle(tier)
    chosen = list(by_tier["sql-contradicts"])
    chosen += by_tier["retrieved-likely"][: max(min(likely_inputs, max_inputs - len(chosen)), 0)]
    for name in ("retrieved", "sql"):
        room = max_inputs - len(chosen)
        chosen += by_tier[name][: max(room, 0)]
    return sorted(chosen, key=lambda c: c.id)


class BuildExistsError(AppError):
    pass


def write_candidate_files(
    engine: Engine,
    embedder: Embedder,
    prices: dict[str, Price] | None,
    *,
    windows: Sequence[BuildWindow],
    set_dir: Path,
    out: Path,
    list_handles: Sequence[str],
    corpus: str,
    max_inputs: int = MAX_INPUTS,
    seed: int = 19,
    force: bool = False,
) -> tuple[int, BuildInfo]:
    manifest = load_manifest(set_dir) or SetManifest(version=set_dir.name)
    previous = manifest.build
    if previous is not None and not force:
        raise BuildExistsError(f"{set_dir / MANIFEST_FILE} already has a build; pass --force")
    # An extra draw keeps the recorded windows, so it draws from the same posts.
    use_windows = list(previous.windows) if previous is not None else list(windows)
    built = build_window_candidates(engine, embedder, use_windows, prices)
    sampled = sample_candidates(built.candidates, max_inputs, seed)
    write_candidates(out, sampled)

    embeddings_cost, extraction_cost = build_costs(engine)
    models = scratch_models(engine)
    if previous is None:
        info = BuildInfo(
            corpus=corpus,
            windows=use_windows,
            embedding_model=models.embedding_model or embedder.model,
            extraction_model=models.extraction_model or "unknown",
            extraction_prompt_version=models.extraction_prompt_version or "unknown",
            embeddings_cost_usd=embeddings_cost,
            extraction_cost_usd=extraction_cost,
            query_embeddings_cost_usd=built.query_embeddings_cost_usd,
            candidates=len(built.candidates),
            judge_calls_per_month=built.judge_calls_per_month,
        )
    else:
        info = previous.model_copy(
            update={
                "query_embeddings_cost_usd": previous.query_embeddings_cost_usd
                + built.query_embeddings_cost_usd
            }
        )
    manifest.build = info
    if not manifest.list_handles:
        manifest.list_handles = list(list_handles)
    write_manifest(set_dir, manifest)
    return len(sampled), info


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


def select_new_cases(
    cases: Sequence[JudgeCase],
    size: int = NEW_CASES_SIZE,
    seed: int = 7,
    min_contradicts: int = MIN_NEW_CONTRADICTS,
    unrelated_cap: float = UNRELATED_CAP,
) -> list[JudgeCase]:
    rng = random.Random(seed)
    ordered = sorted(cases, key=lambda c: c.id)
    retrieved = [c for c in ordered if c.expected == "contradicts" and not c.has_player_event]
    sql_pairs = [c for c in ordered if c.expected == "contradicts" and c.has_player_event]
    rng.shuffle(retrieved)
    rng.shuffle(sql_pairs)
    chosen: list[JudgeCase] = retrieved[:size]
    for case in sql_pairs:
        if len(chosen) >= min(min_contradicts, size):
            break
        chosen.append(case)

    buckets: dict[str, list[JudgeCase]] = {
        label: [c for c in ordered if c.expected == label]
        for label in LABELS
        if label != "contradicts"
    }
    for bucket in buckets.values():
        rng.shuffle(bucket)
        bucket.sort(key=lambda c: not is_known_miss(c))
    unrelated_limit = int(size * unrelated_cap)
    taken = {label: 0 for label in buckets}
    while len(chosen) < size:
        progressed = False
        for label, bucket in buckets.items():
            if len(chosen) >= size:
                break
            if taken[label] >= len(bucket):
                continue
            if label == "unrelated" and taken[label] >= unrelated_limit:
                continue
            chosen.append(bucket[taken[label]])
            taken[label] += 1
            progressed = True
        if not progressed:
            break
    return sorted(chosen, key=lambda c: c.id)


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
