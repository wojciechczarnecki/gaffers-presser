import random
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from app.extraction.linking import normalise
from app.retrieval.evaluation.dataset import PlayerQuery
from app.retrieval.evaluation.labels import AnyQuery, judge_entry, reviewed_entry

# A pair's stratum is the judges that labelled it and its category. Relevant pairs are a few
# per cent of a pool, so the owner reviews the categories at different rates, and every
# estimate weights a reviewed pair by its stratum's size over its reviewed pairs.
CATEGORIES = ("disagreement", "relevant", "named", "rest")

Pair = tuple[str, int]
StratumKey = tuple[tuple[str, ...], str]


def mentions_player(query: AnyQuery, text: str) -> bool:
    if not isinstance(query, PlayerQuery):
        return False
    player = query.player
    names = {normalise(name) for name in (player.web_name, player.second_name, *player.aliases)}
    haystack = normalise(text)
    # Up to three letters of an ending, as Polish posts inflect names (Haalanda, Salahem).
    return any(
        name and re.search(rf"\b{re.escape(name)}[a-z]{{0,3}}\b", haystack) for name in names
    )


def _judge_labels(query: AnyQuery, x_id: int) -> dict[str, bool]:
    labels: dict[str, bool] = {}
    for entry in query.judgements:
        if entry.x_id == x_id and not entry.reviewed:
            labels[entry.labelled_by] = entry.relevant
    return labels


def pair_stratum(query: AnyQuery, x_id: int, text: str) -> StratumKey | None:
    labels = _judge_labels(query, x_id)
    if not labels:
        return None
    judges = tuple(sorted(labels))
    values = set(labels.values())
    if len(values) > 1:
        return judges, "disagreement"
    if values == {True}:
        return judges, "relevant"
    return judges, "named" if mentions_player(query, text) else "rest"


def strata(queries: Sequence[AnyQuery], texts: Mapping[int, str]) -> dict[StratumKey, list[Pair]]:
    found: dict[StratumKey, list[Pair]] = {}
    for query in queries:
        for x_id in sorted(dict.fromkeys(j.x_id for j in query.judgements)):
            key = pair_stratum(query, x_id, texts.get(x_id, ""))
            if key is not None:
                found.setdefault(key, []).append((query.id, x_id))
    return found


def _reviewed(queries: Sequence[AnyQuery]) -> set[Pair]:
    return {
        (query.id, x_id)
        for query in queries
        for x_id in {j.x_id for j in query.judgements}
        if reviewed_entry(query.judgements, x_id) is not None
    }


def stratified_order(
    queries: Sequence[AnyQuery], texts: Mapping[int, str], sizes: Mapping[str, int], seed: int
) -> list[Pair]:
    # Every disagreement, then a seeded sample of each other stratum. The sample is drawn over
    # the whole stratum, reviewed pairs included, and those are skipped afterwards: a resumed
    # review shows the same sample.
    done = _reviewed(queries)
    found = strata(queries, texts)
    order: list[Pair] = []
    for category in CATEGORIES:
        for key in sorted(k for k in found if k[1] == category):
            pairs = found[key]
            if category != "disagreement":
                rng = random.Random(f"{seed}:{'+'.join(key[0])}:{category}")
                pairs = sorted(rng.sample(pairs, min(sizes.get(category, 0), len(pairs))))
            order += [pair for pair in pairs if pair not in done]
    return order


@dataclass(frozen=True)
class OwnerAgreement:
    reviewed: int  # pairs with both the owner's and the judge's label
    unreviewed_pairs: int  # the judge's pairs in strata with no reviewed pair, left out
    agreement: float | None
    precision: float | None
    recall: float | None
    kappa: float | None


def _ratio(top: float, bottom: float) -> float | None:
    return top / bottom if bottom else None


def owner_agreement(
    queries: Sequence[AnyQuery],
    texts: Mapping[int, str],
    judge: str,
    within: Collection[str] = (),
) -> OwnerAgreement:
    # The judge's labels against the owner's, as estimates over every pair the judge labelled
    # (or only the pairs every judge in `within` labelled too): each reviewed pair counts for
    # its stratum's size over its reviewed pairs. A stratum with no reviewed pair is left out
    # and counted, so the estimates cover the pool only when every stratum was sampled.
    by_query = {query.id: query for query in queries}
    tp = fp = fn = tn = 0.0
    reviewed = unreviewed = 0
    for (judges, _), pairs in strata(queries, texts).items():
        if judge not in judges or not set(within) <= set(judges):
            continue
        compared = []
        for query_id, x_id in pairs:
            query = by_query[query_id]
            owner = reviewed_entry(query.judgements, x_id)
            entry = judge_entry(query.judgements, x_id, judge)
            if owner is not None and entry is not None:
                compared.append((owner.relevant, entry.relevant))
        if not compared:
            unreviewed += len(pairs)
            continue
        reviewed += len(compared)
        weight = len(pairs) / len(compared)
        for truth, label in compared:
            if truth and label:
                tp += weight
            elif label:
                fp += weight
            elif truth:
                fn += weight
            else:
                tn += weight
    total = tp + fp + fn + tn
    kappa = None
    if total:
        observed = (tp + tn) / total
        expected = ((tp + fp) * (tp + fn) + (fn + tn) * (fp + tn)) / total**2
        kappa = _ratio(observed - expected, 1 - expected)
    return OwnerAgreement(
        reviewed=reviewed,
        unreviewed_pairs=unreviewed,
        agreement=_ratio(tp + tn, total),
        precision=_ratio(tp, tp + fp),
        recall=_ratio(tp, tp + fn),
        kappa=kappa,
    )


def cohen_kappa(pairs: Sequence[tuple[bool, bool]]) -> float | None:
    if not pairs:
        return None
    total = len(pairs)
    observed = sum(a == b for a, b in pairs) / total
    first = sum(a for a, _ in pairs) / total
    second = sum(b for _, b in pairs) / total
    expected = first * second + (1 - first) * (1 - second)
    return _ratio(observed - expected, 1 - expected)
