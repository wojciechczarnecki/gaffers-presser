from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from langchain_core.language_models import BaseChatModel
from sqlmodel import Session

from app.core.errors import AppError
from app.core.eval_results import ResultExistsError, check_new_result
from app.core.local_time import format_local, parse_local
from app.fpl.calendar import current_season
from app.llm.chat import structured_kwargs_for
from app.llm.pricing import load_prices
from app.llm.structured import StructuredCaller
from app.retrieval.cli import RetrievalCliDeps, _cost, db_engine, fail, get_deps
from app.retrieval.evaluation.compare import format_compare, load_runs
from app.retrieval.evaluation.dataset import (
    CORPUS_FILE,
    DEFAULT_CORPUS_PATH,
    DEFAULT_QUERIES_PATH,
    DEFAULT_RESULTS_DIR,
    DEFAULT_SET_DIR,
    QUERIES_FILE,
    V1_DIR,
    CorpusPost,
    FreeQuery,
    Judgement,
    PlayerQuery,
    Query,
    SetError,
    SetManifest,
    carry_v1,
    export_corpus,
    load_corpus,
    load_queries,
    load_set,
    write_corpus,
    write_manifest,
    write_queries,
)
from app.retrieval.evaluation.labelling import prelabel
from app.retrieval.evaluation.llm import DEFAULT_LABEL_MODEL
from app.retrieval.evaluation.player_metrics import DEFAULT_DEPTHS
from app.retrieval.evaluation.players import build_player_slice
from app.retrieval.evaluation.queries import (
    QueryCounts,
    build_queries,
    current_events,
    make_query_writer,
)
from app.retrieval.evaluation.runner import (
    DEFAULT_MAX_COST_USD,
    Estimate,
    EvaluationError,
    default_run_name,
    format_player_table,
    format_table,
    run_evaluation,
    write_result,
)
from app.retrieval.variants import has_initial

DEFAULT_DEPTHS_TEXT = ",".join(str(depth) for depth in DEFAULT_DEPTHS)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Retrieval evaluation: set building, labelling, review and runs.",
)


@app.callback()
def _callback() -> None:
    # Keeps `app` a multi-command group whatever the number of commands.
    pass


def _parse_time_option(value: str, option: str) -> datetime:
    try:
        return parse_local(value)
    except ValueError:
        raise fail(f"{option} must be YYYY-MM-DD or YYYY-MM-DDTHH:MM") from None


@app.command(name="export-corpus", help="Export the stored posts (public fields only) as a set.")
def export_corpus_command(
    ctx: typer.Context,
    set_dir: Annotated[Path, typer.Option("--set", help="The set directory.")] = DEFAULT_SET_DIR,
    since: Annotated[str | None, typer.Option("--since", help="Europe/Warsaw time.")] = None,
    until: Annotated[str | None, typer.Option("--until", help="Europe/Warsaw time.")] = None,
    version: Annotated[str, typer.Option("--version", help="The set version.")] = "v2",
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing file.")] = False,
) -> None:
    deps = get_deps(ctx)
    since_at = _parse_time_option(since, "--since") if since is not None else None
    until_at = _parse_time_option(until, "--until") if until is not None else None
    output = set_dir / CORPUS_FILE
    try:
        check_new_result(output, force)
    except ResultExistsError as exc:
        raise fail(str(exc)) from None
    exported = export_corpus(db_engine(deps), since_at, until_at)
    write_corpus(output, exported.posts)
    write_manifest(
        set_dir,
        SetManifest(
            version=version,
            list_handles=exported.list_handles,
            corpus_since=since_at,
            corpus_until=until_at,
            exported_at=deps.clock.now(),
        ),
    )
    sources = sum(1 for post in exported.posts if post.is_source)
    typer.echo(f"exported {len(exported.posts)} posts to {output} ({sources} source posts)")


def _chat_model(deps: RetrievalCliDeps, model: str) -> BaseChatModel:
    if deps.make_chat_model is None:
        raise fail("OPENROUTER_API_KEY is not set")
    try:
        return deps.make_chat_model(model)
    except AppError as exc:
        raise fail(str(exc)) from None


@app.command(name="build-queries", help="Build the evaluation queries from the corpus.")
def build_queries_command(
    ctx: typer.Context,
    set_dir: Annotated[Path, typer.Option("--set", help="The set directory.")] = DEFAULT_SET_DIR,
    event_en: Annotated[int, typer.Option("--event-en", min=0)] = QueryCounts.event_en,
    event_pl: Annotated[int, typer.Option("--event-pl", min=0)] = QueryCounts.event_pl,
    post_en: Annotated[int, typer.Option("--post-en", min=0)] = QueryCounts.post_en,
    post_pl: Annotated[int, typer.Option("--post-pl", min=0)] = QueryCounts.post_pl,
    seed: Annotated[int, typer.Option("--seed")] = 6,
    model: Annotated[str | None, typer.Option("--model", help="Chat model ID.")] = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing file.")] = False,
) -> None:
    deps = get_deps(ctx)
    output = set_dir / QUERIES_FILE
    if output.exists() and not force:
        raise fail(f"{output} already exists; pass --force to overwrite it")
    chat_model_id = model or DEFAULT_LABEL_MODEL
    chat_model = _chat_model(deps, chat_model_id)
    try:
        prices = deps.prices if deps.prices is not None else load_prices()
        posts = load_corpus(set_dir / CORPUS_FILE)
    except (AppError, OSError, ValueError) as exc:
        raise fail(f"cannot read the inputs: {type(exc).__name__}") from None
    events = current_events(db_engine(deps), [post.x_id for post in posts])
    caller = StructuredCaller(
        chat_model,
        chat_model_id,
        prices,
        deps.clock,
        structured_kwargs=structured_kwargs_for(chat_model_id),
    )
    result = build_queries(
        posts,
        events,
        make_query_writer(caller),
        QueryCounts(event_en, event_pl, post_en, post_pl),
        seed,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    write_queries(output, result.queries)
    typer.echo(f"queries: {len(result.queries)} written to {output}")
    for origin in ("event", "post"):
        for language in ("en", "pl"):
            count = sum(1 for q in result.queries if (q.origin, q.language) == (origin, language))
            typer.echo(f"  {origin} {language}: {count}")
    typer.echo(f"skipped posts: {result.skipped_posts}")
    typer.echo(f"total cost: {_cost(caller.cost_usd)}")


@app.command(name="build-player-slice", help="Build the player queries for a gameweek deadline.")
def build_player_slice_command(
    ctx: typer.Context,
    gameweek: Annotated[int, typer.Option("--gameweek", min=1, help="The deadline's gameweek.")],
    season: Annotated[str | None, typer.Option("--season")] = None,
    window: Annotated[
        str, typer.Option("--window", help="alerts (default) or after-last-match.")
    ] = "alerts",
    seed: Annotated[int, typer.Option("--seed")] = 6,
    set_dir: Annotated[Path, typer.Option("--set", help="The set directory.")] = DEFAULT_SET_DIR,
    force: Annotated[bool, typer.Option("--force", help="Replace the player queries.")] = False,
) -> None:
    if window not in ("alerts", "after-last-match"):
        raise fail("--window must be alerts or after-last-match")
    deps = get_deps(ctx)
    engine = db_engine(deps)
    queries_path = set_dir / QUERIES_FILE
    existing = load_queries(queries_path) if queries_path.exists() else []
    if any(isinstance(query, PlayerQuery) for query in existing) and not force:
        raise fail(f"{queries_path} already holds player queries; pass --force to replace them")
    try:
        chosen = season
        if chosen is None:
            with Session(engine) as session:
                chosen = current_season(session)
        if chosen is None:
            raise fail("the database has no season")
        built = build_player_slice(engine, chosen, gameweek, window, seed)
    except EvaluationError as exc:
        raise fail(str(exc)) from None
    kept = [query for query in existing if not isinstance(query, PlayerQuery)]
    write_queries(queries_path, [*built, *kept])
    claimed = [post for query in built for post in query.claimed]
    typer.echo(f"player queries: {len(built)} written to {queries_path}")
    typer.echo(f"claimed posts: {len(claimed)}  stale: {sum(post.stale for post in claimed)}")
    typer.echo(f"players with an initial: {sum(has_initial(q.player.web_name) for q in built)}")
    corpus_path = set_dir / CORPUS_FILE
    if corpus_path.exists():
        sources = {post.x_id for post in load_corpus(corpus_path) if post.is_source}
        outside = sum(1 for post in claimed if post.x_id not in sources)
        if outside:
            typer.echo(f"warning: {outside} claimed posts are not source posts of the corpus")


@app.command(
    name="carry-v1", help="Carry set v1's queries and judgements into a set as free queries."
)
def carry_v1_command(
    set_dir: Annotated[Path, typer.Option("--set", help="The set directory.")] = DEFAULT_SET_DIR,
    from_dir: Annotated[Path, typer.Option("--from", help="The v1 directory.")] = V1_DIR,
    first: Annotated[int | None, typer.Option("--first", min=1, help="Only the first N.")] = None,
    force: Annotated[bool, typer.Option("--force", help="Replace the free queries.")] = False,
) -> None:
    try:
        v1 = load_set(from_dir)
        target = load_set(set_dir)
        carried = v1.queries[:first] if first is not None else v1.queries
        existing = target.queries
        if force:
            existing = [q for q in existing if not isinstance(q, FreeQuery)]
        queries = carry_v1(carried, existing, {post.x_id for post in target.corpus})
    except SetError as exc:
        raise fail(str(exc)) from None
    write_queries(set_dir / QUERIES_FILE, queries)
    typer.echo(f"carried {len(carried)} queries into {set_dir / QUERIES_FILE}")


@app.command(name="prelabel", help="Pool candidates for every query and pre-label them.")
def prelabel_command(
    ctx: typer.Context,
    set_dir: Annotated[Path, typer.Option("--set", help="The set directory.")] = DEFAULT_SET_DIR,
    model: Annotated[str | None, typer.Option("--model", help="Chat model ID.")] = None,
    embedding_model: Annotated[
        str | None, typer.Option("--embedding-model", help="Embedding model ID.")
    ] = None,
) -> None:
    deps = get_deps(ctx)
    chat_model_id = model or DEFAULT_LABEL_MODEL
    chat_model = _chat_model(deps, chat_model_id)
    try:
        embedder = deps.make_embedder(embedding_model)
        prices = deps.prices if deps.prices is not None else load_prices()
        posts = load_corpus(set_dir / CORPUS_FILE)
        query_set = load_queries(set_dir / QUERIES_FILE)
    except AppError as exc:
        raise fail(str(exc)) from None
    except (OSError, ValueError) as exc:
        raise fail(f"cannot read the inputs: {type(exc).__name__}") from None
    queries = set_dir / QUERIES_FILE
    caller = StructuredCaller(
        chat_model,
        chat_model_id,
        prices,
        deps.clock,
        structured_kwargs=structured_kwargs_for(chat_model_id),
    )
    tracer = deps.make_tracer()
    try:
        summary = prelabel(
            db_engine(deps),
            posts,
            query_set,
            queries,
            embedder,
            caller,
            prices,
            tracer,
            deps.clock,
        )
    except EvaluationError as exc:
        raise fail(str(exc)) from None
    finally:
        tracer.flush()
    typer.echo(f"queries labelled: {summary.queries_labelled}")
    typer.echo(f"candidates: {summary.candidates}")
    typer.echo(f"relevant: {summary.relevant}")
    typer.echo(f"label failures: {summary.failures}")
    if summary.failures:
        typer.echo("queries with a label failure were left unlabelled; run prelabel again")
    typer.echo(f"total cost: {_cost(summary.cost_usd)}")


@app.command(name="evaluate", help="Report the player slice at the judge's cut and the free slice.")
def evaluate_command(
    ctx: typer.Context,
    split: Annotated[str, typer.Option("--split", help="dev or test.")],
    reference_judge: Annotated[
        str, typer.Option("--reference-judge", help="The judge whose labels are the reference.")
    ],
    embedding_model: Annotated[
        str | None, typer.Option("--embedding-model", help="Embedding model ID.")
    ] = None,
    depths: Annotated[
        str, typer.Option("--depths", help="Search depths, e.g. 20,30,50.")
    ] = ",".join(str(depth) for depth in DEFAULT_DEPTHS),
    k: Annotated[int, typer.Option("--k", min=0)] = 60,
    max_cost_usd: Annotated[float, typer.Option("--max-cost-usd", min=0)] = DEFAULT_MAX_COST_USD,
    set_dir: Annotated[Path, typer.Option("--set", help="The set directory.")] = DEFAULT_SET_DIR,
    output: Annotated[Path | None, typer.Option("--output")] = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing result.")] = False,
) -> None:
    if split not in ("dev", "test"):
        raise fail("--split must be dev or test")
    try:
        chosen_depths = [int(part) for part in depths.split(",") if part.strip()]
        if not chosen_depths or min(chosen_depths) < 1:
            raise ValueError
    except ValueError:
        raise fail("--depths must be positive whole numbers separated by commas") from None
    deps = get_deps(ctx)
    try:
        embedder = deps.make_embedder(embedding_model)
        prices = deps.prices if deps.prices is not None else load_prices()
        eval_set = load_set(set_dir)
    except AppError as exc:
        raise fail(str(exc)) from None
    except (OSError, ValueError) as exc:
        raise fail(f"cannot read the inputs: {type(exc).__name__}") from None
    # Dev runs are working iterations: results/dev/ is gitignored.
    directory = DEFAULT_RESULTS_DIR / "dev" if split == "dev" else DEFAULT_RESULTS_DIR
    path = output or directory / f"{default_run_name(split, eval_set.version, embedder.model)}.json"
    try:
        check_new_result(path, force)
    except ResultExistsError as exc:
        raise fail(str(exc)) from None

    def show_estimate(estimate: Estimate) -> None:
        typer.echo(
            f"estimated embedding: {estimate.tokens} tokens, ${estimate.cost_usd:.6f}"
            f" ({embedder.model})"
        )

    tracer = deps.make_tracer()
    try:
        result = run_evaluation(
            db_engine(deps),
            eval_set,
            embedder,
            tracer,
            prices,
            deps.clock,
            split=split,
            reference_judge=reference_judge,
            depths=chosen_depths,
            k=k,
            max_cost_usd=max_cost_usd,
            on_estimate=show_estimate,
        )
    except EvaluationError as exc:
        raise fail(str(exc)) from None
    finally:
        tracer.flush()
    if result.player_rows:
        typer.echo(format_player_table(result.player_rows, result.player_counts))
    typer.echo(format_table(result.free))
    write_result(path, result.data)
    typer.echo(f"result written to {path}")


@app.command(help="Print one table from several result files of the same set and split.")
def compare(
    files: Annotated[list[Path], typer.Argument(help="Result files written by evaluate.")],
) -> None:
    try:
        text = format_compare(load_runs(files))
    except EvaluationError as exc:
        raise fail(str(exc)) from None
    typer.echo(text)


REVIEW_ACTIONS = "[a]ccept  [f]lip  [s]kip  [+] add post  [n]ext query  [q]uit"


def _render_judgement(
    query: Query, judgement: Judgement, post: CorpusPost | None, progress: str
) -> str:
    label = "relevant" if judgement.relevant else "not relevant"
    lines = [
        "─" * 70,
        progress,
        f"{query.id}  [{query.language}, {query.origin}]  {query.text}",
    ]
    if post is None:
        lines.append(f"post {judgement.x_id}: not in the corpus")
    else:
        lines.append(f"@{post.author_handle}  {format_local(post.created_at)}  {post.x_id}")
        lines.append(post.text)
    lines.append(f"{judgement.labelled_by} says: {label}")
    return "\n".join(lines)


def _save_query(queries: list[Query], index: int, judgements: list[Judgement], path: Path) -> None:
    queries[index] = queries[index].model_copy(update={"judgements": judgements})
    write_queries(path, queries)


def _review_queries(
    queries: list[Query],
    positions: list[int],
    corpus: dict[int, CorpusPost],
    path: Path,
    counts: dict[str, int],
) -> None:
    # `queries` is saved whole after every change.
    for done, position in enumerate(positions):
        pending = [j.x_id for j in queries[position].judgements if not j.reviewed]
        for x_id in pending:
            while True:
                current = queries[position].judgements
                judgement = next((j for j in current if j.x_id == x_id), None)
                if judgement is None or judgement.reviewed:
                    break  # replaced by an added post
                reviewed = sum(j.reviewed for q in queries for j in q.judgements)
                total = sum(len(q.judgements) for q in queries)
                progress = (
                    f"{reviewed}/{total} labels reviewed,"
                    f" {len(positions) - done} queries in this run"
                )
                typer.echo(
                    _render_judgement(queries[position], judgement, corpus.get(x_id), progress)
                )
                action = typer.prompt(REVIEW_ACTIONS).strip().lower()
                if action in ("a", "f"):
                    update = {"reviewed": True}
                    if action == "f":
                        update["relevant"] = not judgement.relevant
                    replaced = [
                        j.model_copy(update=update) if j is judgement else j for j in current
                    ]
                    _save_query(queries, position, replaced, path)
                    counts["accepted" if action == "a" else "flipped"] += 1
                    break
                if action == "s":
                    counts["skipped"] += 1
                    break
                if action == "+":
                    added = _add_post(queries, position, corpus, path)
                    if added:
                        counts["added"] += 1
                elif action == "n":
                    break
                elif action == "q":
                    return
                else:
                    typer.echo(f"unknown action {action!r}")
            if action == "n":
                break


def _add_post(
    queries: list[Query], position: int, corpus: dict[int, CorpusPost], path: Path
) -> bool:
    raw = typer.prompt("X ID of the relevant post").strip()
    if not raw.isdigit() or int(raw) not in corpus:
        typer.echo(f"{raw!r} is not in the corpus")
        return False
    added = Judgement(x_id=int(raw), relevant=True, reviewed=True, labelled_by="owner")
    kept = [j for j in queries[position].judgements if j.x_id != added.x_id]
    _save_query(queries, position, [*kept, added], path)
    typer.echo("saved")
    return True


@app.command(help="Review the pre-labelled judgements: accept, flip, skip or add a post.")
def review(
    queries_path: Annotated[Path, typer.Option("--queries")] = DEFAULT_QUERIES_PATH,
    corpus_path: Annotated[Path, typer.Option("--corpus")] = DEFAULT_CORPUS_PATH,
    split: Annotated[str | None, typer.Option("--split")] = None,
) -> None:
    # Needs no database and no key, so it never builds RetrievalCliDeps.
    if split not in (None, "dev", "test"):
        raise fail("--split must be dev or test")
    try:
        queries = load_queries(queries_path)
        corpus = {post.x_id: post for post in load_corpus(corpus_path)}
    except (OSError, ValueError) as exc:
        raise fail(f"cannot read the inputs: {type(exc).__name__}") from None
    positions = [
        i
        for i, query in enumerate(queries)
        if any(not j.reviewed for j in query.judgements) and split in (None, query.split)
    ]
    if not positions:
        typer.echo("nothing to review")
        return
    counts = {"accepted": 0, "flipped": 0, "skipped": 0, "added": 0}
    interrupted = False
    try:
        _review_queries(queries, positions, corpus, queries_path, counts)
    except (typer.Abort, KeyboardInterrupt):
        interrupted = True  # every decision is already on disk
    reviewed = sum(j.reviewed for q in queries for j in q.judgements)
    typer.echo("── summary " + "─" * 61)
    typer.echo(
        f"accepted: {counts['accepted']}  flipped: {counts['flipped']}"
        f"  skipped: {counts['skipped']}  added: {counts['added']}"
    )
    typer.echo(f"reviewed: {reviewed}/{sum(len(q.judgements) for q in queries)}")
    if interrupted:
        raise typer.Exit(130)


def main() -> None:
    app(prog_name="python -m app.retrieval.evaluation")
