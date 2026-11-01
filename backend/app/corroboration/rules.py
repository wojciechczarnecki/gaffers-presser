from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from app.corroboration.schemas import (
    Claim,
    FlagState,
    Freshness,
    Grade,
    Label,
    LabelledPost,
    PostRef,
)


def _full_credibility(account: str) -> float:
    return 1.0


@dataclass(frozen=True)
class GradeRules:
    high_min_supporting: float = 2
    medium_min_supporting: float = 1
    confirmed_high_min_supporting: float = 1
    credibility: Callable[[str], float] = field(default=_full_credibility)


@dataclass(frozen=True)
class AccountCounts:
    supporting: list[LabelledPost]
    contradicting: list[LabelledPost]
    related: list[LabelledPost]


DEFAULT_RULES = GradeRules()
_AVAILABILITY = {"out", "doubt", "benched"}
_ABSENCES = ("out", "doubt")
_LEVELS = ("low", "medium", "high")


def label_claim(anchor_type: str, claim_type: str) -> Label:
    if anchor_type == claim_type:
        return "supports"
    if "available" in (anchor_type, claim_type):
        other = claim_type if anchor_type == "available" else anchor_type
        return "contradicts" if other in _ABSENCES else "related"
    pair = {anchor_type, claim_type}
    if pair == {"benched", "out"}:
        return "contradicts"
    if "confirmed_starter" in pair and pair - {"confirmed_starter"} <= _AVAILABILITY:
        return "contradicts"
    return "related"


def _claim_key(claim: Claim) -> tuple[datetime, int]:
    return (claim.post.created_at, claim.post.x_id)


def flag_at(flags: Sequence[FlagState], moment: datetime) -> FlagState | None:
    seen = [flag for flag in flags if flag.observed_at <= moment]
    return max(seen, key=lambda flag: flag.observed_at, default=None)


def reversed_claim(anchor: Claim, claims: Sequence[Claim]) -> Claim | None:
    older = [claim for claim in claims if _claim_key(claim) < _claim_key(anchor)]
    if not older:
        return None
    newest = max(older, key=_claim_key)
    if label_claim(anchor.event_type, newest.event_type) == "contradicts":
        return newest
    return None


@dataclass(frozen=True)
class AnchorChoice:
    anchor: Claim
    reversed_claim: Claim | None = None
    reversed_flag: FlagState | None = None


def choose_anchor(
    claims: Sequence[Claim], flags: Sequence[FlagState], anchor_x_id: int | None = None
) -> AnchorChoice | None:
    if not claims:
        return None
    newest = max(claims, key=_claim_key)
    candidate = next((c for c in claims if c.post.x_id == anchor_x_id), None) or newest
    if candidate.event_type != "available":
        return AnchorChoice(candidate, reversed_claim(candidate, claims))
    if candidate is newest:
        earlier = reversed_claim(candidate, claims)
        flag = flag_at(flags, candidate.post.created_at)
        flagged = flag if flag is not None and flag.status != "a" else None
        if earlier is not None or flagged is not None:
            return AnchorChoice(candidate, earlier, None if earlier is not None else flagged)
    others = [claim for claim in claims if claim.event_type != "available"]
    if not others:
        return None
    fallback = max(others, key=_claim_key)
    if label_claim("available", fallback.event_type) == "contradicts":
        return None
    return AnchorChoice(fallback, reversed_claim(fallback, claims))


def account_of(post: PostRef) -> str:
    return post.original_author.lower()


def counted_account(post: PostRef, label: Label) -> str:
    if label != "contradicts" and post.quoted_author_handle:
        return post.quoted_author_handle.lower()
    return account_of(post)


def _newest_first(items: Sequence[LabelledPost]) -> list[LabelledPost]:
    return sorted(items, key=lambda item: (item.post.created_at, item.post.x_id), reverse=True)


def count_accounts(labelled: Sequence[LabelledPost], anchor: PostRef) -> AccountCounts:
    anchor_accounts = {account_of(anchor), counted_account(anchor, "supports")}
    newest: dict[str, LabelledPost] = {}
    for item in _newest_first(labelled):
        if item.post.x_id == anchor.x_id or item.label == "unrelated":
            continue
        newest.setdefault(counted_account(item.post, item.label), item)
    counts = AccountCounts([], [], [])
    for account, item in newest.items():
        if item.label == "supports":
            if account not in anchor_accounts:
                counts.supporting.append(item)
        elif item.label == "contradicts":
            if account not in anchor_accounts:
                counts.contradicting.append(item)
        else:
            counts.related.append(item)
    return counts


def freshness(created_at: datetime, new_since: datetime) -> Freshness:
    return "new" if created_at > new_since else "context"


def newer_contradiction(contradicting: Sequence[LabelledPost], anchor: PostRef) -> bool:
    return any(
        item.origin == "judge" and item.post.created_at > anchor.created_at
        for item in contradicting
    )


def _count(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def grade(
    certainty: str,
    supporting: Sequence[LabelledPost],
    contradicting: Sequence[LabelledPost],
    newer_contradiction: bool,
    new_since: datetime,
    rules: GradeRules = DEFAULT_RULES,
) -> Grade:
    weight = sum(rules.credibility(counted_account(item.post, item.label)) for item in supporting)
    support = f"{_count(weight)} independent supporting account(s)"
    reasons: list[str] = []
    confirmed = certainty == "confirmed"
    high_by_weight = weight >= rules.high_min_supporting
    high_by_confirmed = confirmed and weight >= rules.confirmed_high_min_supporting
    if high_by_weight or high_by_confirmed:
        level = 2
        if high_by_confirmed:
            reasons.append(
                f"the anchor's certainty is confirmed and {support}, at least "
                f"{_count(rules.confirmed_high_min_supporting)} -> high"
            )
        if high_by_weight:
            reasons.append(f"{support}, at least {_count(rules.high_min_supporting)} -> high")
    elif confirmed or certainty == "likely" or weight >= rules.medium_min_supporting:
        level = 1
        if confirmed or certainty == "likely":
            reasons.append(f"the anchor's certainty is {certainty} -> medium")
        if weight >= rules.medium_min_supporting:
            reasons.append(f"{support}, at least {_count(rules.medium_min_supporting)} -> medium")
    else:
        level = 0
        reasons.append(f"certainty {certainty} and {support} -> low")

    lowering: list[str] = []
    if any(freshness(item.post.created_at, new_since) == "new" for item in contradicting):
        lowering.append("a contradicting account is new since the last look")
    if newer_contradiction:
        lowering.append("a contradicting post is newer than the anchor")
    if lowering:
        if level > 0:
            level -= 1
            reasons.extend(f"{reason}: lowered one step" for reason in lowering)
        else:
            reasons.extend(f"{reason}: already low" for reason in lowering)
    return Grade(_LEVELS[level], tuple(reasons))
