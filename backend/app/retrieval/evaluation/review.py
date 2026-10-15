import random
from collections.abc import Mapping, Sequence

from app.core.local_time import format_local
from app.retrieval.evaluation.dataset import CorpusPost, Judgement, PlayerQuery
from app.retrieval.evaluation.labels import OWNER, AnyQuery, reviewed_entry

RULE = "─" * 70
ACTIONS = "[r]elevant  [n]ot relevant  [s]kip  [q]uit"

Pair = tuple[str, int]


def _judge_labels(query: AnyQuery, x_id: int) -> set[bool]:
    return {j.relevant for j in query.judgements if j.x_id == x_id and not j.reviewed}


def review_order(queries: Sequence[AnyQuery], sample: int, seed: int) -> list[Pair]:
    # The sample is drawn over every judge-labelled pair with no disagreement, reviewed ones
    # included, and the reviewed ones are skipped afterwards: a resumed review shows the same
    # sample.
    disagreements: list[Pair] = []
    others: list[Pair] = []
    for query in queries:
        for x_id in sorted(dict.fromkeys(j.x_id for j in query.judgements)):
            labels = _judge_labels(query, x_id)
            if not labels:
                continue
            reviewed = reviewed_entry(query.judgements, x_id) is not None
            if len(labels) > 1:
                if not reviewed:
                    disagreements.append((query.id, x_id))
            else:
                others.append((query.id, x_id))
    chosen = random.Random(seed).sample(others, min(sample, len(others)))
    done = {
        (query.id, x_id)
        for query in queries
        for x_id in {j.x_id for j in query.judgements}
        if reviewed_entry(query.judgements, x_id) is not None
    }
    return [*disagreements, *[pair for pair in sorted(chosen) if pair not in done]]


def render_pair(query: AnyQuery, post: CorpusPost | None, x_id: int, progress: str) -> str:
    lines = [RULE, progress]
    if isinstance(query, PlayerQuery):
        player = query.player
        lines += [
            f"{player.first_name} {player.second_name} ({player.web_name}), {player.team}",
            f"Relevant: availability for his gameweek {query.gameweek} match"
            f" (deadline {format_local(query.deadline_at)}), denials included",
            f"Window: {format_local(query.window_start)} to {format_local(query.window_end)}",
        ]
    else:
        lines.append(f"{query.id}  [{query.language}, {query.origin}]  {query.text}")
    if post is None:
        lines.append(f"post {x_id}: not in the corpus")
    else:
        lines.append(f"@{post.author_handle}  {format_local(post.created_at)}  {post.x_id}")
        lines.append(post.text)
    return "\n".join(lines)


def with_owner_label(query: AnyQuery, x_id: int, relevant: bool) -> AnyQuery:
    kept = [j for j in query.judgements if not (j.x_id == x_id and j.labelled_by == OWNER)]
    entry = Judgement(x_id=x_id, relevant=relevant, reviewed=True, labelled_by=OWNER)
    return query.model_copy(update={"judgements": [*kept, entry]})


def reviewed_total(queries: Sequence[AnyQuery]) -> Mapping[str, int]:
    pairs = {
        (query.id, x_id)
        for query in queries
        for x_id in {j.x_id for j in query.judgements}
        if reviewed_entry(query.judgements, x_id) is not None
    }
    return {"reviewed": len(pairs)}
