import json
import random
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.core.eval_results import write_text_atomic
from app.extraction.linking import normalise
from app.retrieval.evaluation.dataset import PlayerQuery
from app.retrieval.evaluation.labels import AnyQuery, judge_entry, reviewed_entry

# A pair's stratum is the judges that labelled it and its category. Relevant pairs are a few
# per cent of a pool, so the owner reviews the categories at different rates, and every
# estimate weights a reviewed pair by its stratum's size over its reviewed pairs.
CATEGORIES = ("disagreement", "relevant", "named", "rest")
STRATA_FILE = "strata.json"
# A pair labelled after the strata were frozen falls in a stratum of its own, with no review.
NEW = " (new)"

Pair = tuple[str, int]
StratumKey = tuple[tuple[str, ...], str]
Strata = dict[StratumKey, list[Pair]]


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


def strata(
    queries: Sequence[AnyQuery], texts: Mapping[int, str], frozen: Strata | None = None
) -> Strata:
    # A re-judged pair can change its stratum, and a reviewed pair would then weigh in a stratum
    # it was not sampled from. With frozen strata (saved when the review starts) every pair keeps
    # the stratum it had then; pairs labelled later fall in their own strata (NEW).
    by_query = {query.id: query for query in queries}
    found: Strata = {}
    known: set[Pair] = set()
    if frozen is not None:
        for key, pairs in frozen.items():
            kept = [pair for pair in pairs if pair[0] in by_query]
            known.update(pairs)
            if kept:
                found[key] = kept
    for query in queries:
        for x_id in sorted(dict.fromkeys(j.x_id for j in query.judgements)):
            if (query.id, x_id) in known:
                continue
            key = pair_stratum(query, x_id, texts.get(x_id, ""))
            if key is not None:
                if frozen is not None:
                    key = (key[0], key[1] + NEW)
                found.setdefault(key, []).append((query.id, x_id))
    return found


def save_strata(path: Path, found: Strata) -> None:
    # One stratum per line: the file holds every judged pair of the slice.
    lines = [
        json.dumps(
            {"judges": list(judges), "category": category, "pairs": [list(p) for p in pairs]},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        for (judges, category), pairs in sorted(found.items())
    ]
    write_text_atomic(path, '{"strata": [\n' + ",\n".join(lines) + "\n]}\n")


def load_strata(path: Path) -> Strata | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        (tuple(item["judges"]), item["category"]): [(q, int(x)) for q, x in item["pairs"]]
        for item in data["strata"]
    }


def _reviewed(queries: Sequence[AnyQuery]) -> set[Pair]:
    return {
        (query.id, x_id)
        for query in queries
        for x_id in {j.x_id for j in query.judgements}
        if reviewed_entry(query.judgements, x_id) is not None
    }


def stratified_order(
    queries: Sequence[AnyQuery],
    texts: Mapping[int, str],
    sizes: Mapping[str, int],
    seed: int,
    frozen: Strata | None = None,
) -> list[Pair]:
    # Every disagreement, then a seeded sample of each other stratum. The sample is drawn over
    # the whole stratum, reviewed pairs included, and those are skipped afterwards: a resumed
    # review shows the same sample.
    done = _reviewed(queries)
    found = strata(queries, texts, frozen)
    order: list[Pair] = []
    for category in CATEGORIES:
        for key in sorted(k for k in found if k[1].removesuffix(NEW) == category):
            pairs = found[key]
            if category != "disagreement":
                rng = random.Random(f"{seed}:{'+'.join(key[0])}:{key[1]}")
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
    frozen: Strata | None = None,
) -> OwnerAgreement:
    # The judge's labels against the owner's, as estimates over every pair the judge labelled
    # (or only the pairs every judge in `within` labelled too): each reviewed pair counts for
    # its stratum's size over its reviewed pairs. A stratum with no reviewed pair is left out
    # and counted, so the estimates cover the pool only when every stratum was sampled.
    by_query = {query.id: query for query in queries}
    tp = fp = fn = tn = 0.0
    reviewed = unreviewed = 0
    for (judges, _), pairs in strata(queries, texts, frozen).items():
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
