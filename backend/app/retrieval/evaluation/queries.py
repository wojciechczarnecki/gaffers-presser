import logging
import random
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Session

from app.content import PROMPTS_DIR, load_prompt
from app.extraction.store import current_extractions
from app.llm.structured import StructuredCaller
from app.retrieval.evaluation.dataset import CorpusPost, Query, QueryEvent
from app.retrieval.evaluation.llm import WrittenQuery

logger = logging.getLogger(__name__)

TEMPLATES_PATH = PROMPTS_DIR.parent / "retrieval_query_templates.toml"
MIN_POST_CHARACTERS = 40
DEV_SHARE = 0.3
LANGUAGE_NAMES = {"en": "English", "pl": "Polish"}


@dataclass(frozen=True)
class CurrentEvent:
    x_id: int
    player: str
    event_type: str


@dataclass(frozen=True)
class QueryCounts:
    event_en: int = 15
    event_pl: int = 5
    post_en: int = 15
    post_pl: int = 5


DEFAULT_COUNTS = QueryCounts()


@dataclass(frozen=True)
class BuildResult:
    queries: list[Query]
    skipped_posts: int


WriteQuery = Callable[[CorpusPost, str], str]


def load_templates(path: Path = TEMPLATES_PATH) -> dict[str, dict[str, str]]:
    return tomllib.loads(Path(path).read_text(encoding="utf-8"))


def current_events(engine: Engine, x_ids: Sequence[int]) -> list[CurrentEvent]:
    with Session(engine) as session:
        rows = current_extractions(session, x_ids=list(x_ids))
    return [
        CurrentEvent(
            x_id=row.tweet_x_id,
            player=event.player_web_name or event.mention,
            event_type=event.event_type,
        )
        for row in sorted(rows, key=lambda r: r.tweet_x_id)
        for event in row.events
    ]


def make_query_writer(caller: StructuredCaller) -> WriteQuery:
    prompt = load_prompt("retrieval_query")

    def write(post: CorpusPost, language: str) -> str:
        human = (
            f"Language: {LANGUAGE_NAMES[language]}\n\nPost by @{post.author_handle}:\n{post.text}"
        )
        written = caller.call(WrittenQuery, prompt.text, human)
        query = " ".join(written.query.split())
        if not query:
            raise ValueError("empty query")
        return query

    return write


def _split(queries: list[Query]) -> list[Query]:
    dev = round(DEV_SHARE * len(queries))
    return [
        query.model_copy(update={"split": "dev" if index < dev else "test"})
        for index, query in enumerate(queries)
    ]


def build_queries(
    corpus: Sequence[CorpusPost],
    events: Sequence[CurrentEvent],
    write_query: WriteQuery,
    counts: QueryCounts = DEFAULT_COUNTS,
    seed: int = 6,
    templates: dict[str, dict[str, str]] | None = None,
) -> BuildResult:
    templates = templates or load_templates()
    rng = random.Random(seed)
    known = {post.x_id for post in corpus}

    pairs: list[CurrentEvent] = []
    seen_pairs: set[tuple[str, str]] = set()
    for event in events:
        key = (event.player, event.event_type)
        if event.x_id in known and key not in seen_pairs and event.event_type in templates["en"]:
            seen_pairs.add(key)
            pairs.append(event)
    rng.shuffle(pairs)
    event_pairs = {"en": pairs[: counts.event_en]}
    event_pairs["pl"] = pairs[counts.event_en : counts.event_en + counts.event_pl]

    wanted_events = {"en": counts.event_en, "pl": counts.event_pl}
    wanted_posts = {
        "en": counts.post_en + wanted_events["en"] - len(event_pairs["en"]),
        "pl": counts.post_pl + wanted_events["pl"] - len(event_pairs["pl"]),
    }

    eligible = [
        post for post in corpus if not post.is_repost and len(post.text) >= MIN_POST_CHARACTERS
    ]
    rng.shuffle(eligible)
    remaining = iter(eligible)

    built: dict[tuple[str, str], list[Query]] = {}
    for language in ("en", "pl"):
        items = []
        for number, event in enumerate(event_pairs[language], start=1):
            items.append(
                Query(
                    id=f"q-event-{language}-{number:03d}",
                    text=templates[language][event.event_type].format(player=event.player),
                    language=language,
                    origin="event",
                    source_x_id=None,
                    event=QueryEvent(player=event.player, event_type=event.event_type),
                    split="test",
                    judgements=[],
                )
            )
        built[("event", language)] = items

    skipped = 0
    for language in ("en", "pl"):
        items = []
        while len(items) < wanted_posts[language]:
            post = next(remaining, None)
            if post is None:
                break
            try:
                query_text = write_query(post, language)
            except Exception as exc:
                logger.warning("query for a post was not written: %s", type(exc).__name__)
                skipped += 1
                continue
            items.append(
                Query(
                    id=f"q-post-{language}-{len(items) + 1:03d}",
                    text=query_text,
                    language=language,
                    origin="post",
                    source_x_id=post.x_id,
                    event=None,
                    split="test",
                    judgements=[],
                )
            )
        built[("post", language)] = items

    queries = [
        query
        for key in (("event", "en"), ("event", "pl"), ("post", "en"), ("post", "pl"))
        for query in _split(built[key])
    ]
    return BuildResult(queries=queries, skipped_posts=skipped)
