from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from app.corroboration.schemas import (
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
    credibility: Callable[[str], float] = field(default=_full_credibility)


@dataclass(frozen=True)
class AccountCounts:
    supporting: list[LabelledPost]
    contradicting: list[LabelledPost]
    related: list[LabelledPost]


DEFAULT_RULES = GradeRules()
_AVAILABILITY = {"out", "doubt", "benched"}
_LEVELS = ("low", "medium", "high")


def label_claim(anchor_type: str, claim_type: str) -> Label:
    if anchor_type == claim_type:
        return "supports"
    pair = {anchor_type, claim_type}
    if "confirmed_starter" in pair and pair - {"confirmed_starter"} <= _AVAILABILITY:
        return "contradicts"
    return "related"


def account_of(post: PostRef) -> str:
    if post.is_repost and post.reposted_author_handle:
        return post.reposted_author_handle.lower()
    return post.author_handle.lower()


def _newest_first(items: Sequence[LabelledPost]) -> list[LabelledPost]:
    return sorted(items, key=lambda item: (item.post.created_at, item.post.x_id), reverse=True)


def count_accounts(labelled: Sequence[LabelledPost], anchor: PostRef) -> AccountCounts:
    anchor_account = account_of(anchor)
    newest: dict[str, LabelledPost] = {}
    for item in _newest_first(labelled):
        if item.post.x_id == anchor.x_id or item.label == "unrelated":
            continue
        newest.setdefault(account_of(item.post), item)
    counts = AccountCounts([], [], [])
    for account, item in newest.items():
        if item.label == "supports":
            if account != anchor_account:
                counts.supporting.append(item)
        elif item.label == "contradicts":
            counts.contradicting.append(item)
        else:
            counts.related.append(item)
    return counts


def freshness(created_at: datetime, new_since: datetime) -> Freshness:
    return "new" if created_at > new_since else "context"


def reversal(contradicting: Sequence[LabelledPost], anchor: PostRef) -> bool:
    return any(item.post.created_at < anchor.created_at for item in contradicting)


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
    weight = sum(rules.credibility(account_of(item.post)) for item in supporting)
    support = f"{_count(weight)} independent supporting account(s)"
    reasons: list[str] = []
    if certainty == "confirmed" or weight >= rules.high_min_supporting:
        level = 2
        if certainty == "confirmed":
            reasons.append("the anchor's certainty is confirmed -> high")
        if weight >= rules.high_min_supporting:
            reasons.append(f"{support}, at least {_count(rules.high_min_supporting)} -> high")
    elif certainty == "likely" or weight >= rules.medium_min_supporting:
        level = 1
        if certainty == "likely":
            reasons.append("the anchor's certainty is likely -> medium")
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
