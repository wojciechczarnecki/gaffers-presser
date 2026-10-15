import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.retrieval.cli import RetrievalCliDeps, embedder_from_settings
from app.retrieval.config import RetrievalSettings
from app.retrieval.evaluation.cli import app
from app.retrieval.evaluation.dataset import (
    ClaimedPost,
    CorpusPost,
    EvalSet,
    EvaluationError,
    FreeQuery,
    Judgement,
    PlayerInfo,
    PlayerQuery,
    SetManifest,
    source_flags,
    write_corpus,
    write_manifest,
    write_queries,
)
from app.retrieval.evaluation.runner import (
    default_run_name,
    format_player_table,
    format_table,
    run_evaluation,
)
from app.retrieval.search import SearchFilters
from tests.retrieval.fakes import AlwaysFailingEmbedder, FakeEmbedder, RecordingTracer
from tests.retrieval.helpers import MODEL, PRICES, FixedClock
from tests.retrieval.test_cli import _deps

T0 = datetime(2026, 10, 1, tzinfo=UTC)
T1 = datetime(2026, 10, 8, tzinfo=UTC)
INSIDE = datetime(2026, 10, 3, 12, tzinfo=UTC)
JUDGE = "judge-a"


def _post(x_id, text, author="listed", when=INSIDE):
    return CorpusPost(
        x_id=x_id,
        author_handle=author,
        text=text,
        created_at=when + timedelta(minutes=x_id),
        is_repost=False,
        is_reply=False,
    )


POSTS = [
    _post(1, "Saka injury doubt for Sunday"),
    _post(2, "Saka back in training today"),
    _post(3, "Saka news from the old gameweek", when=T0 - timedelta(days=3)),
    _post(4, "Saka rumour from a stranger", author="other"),
    _post(5, "Haaland scores hat-trick"),
    _post(6, "Saka starts according to the line-up"),
    _post(7, "Bruno Fernandes is fit"),
    _post(8, "Fernandes trained today"),
    _post(9, "Kontuzja Sakę wyklucza z gry"),
]
CORPUS = source_flags(POSTS, ["listed"])


def _j(x_id, relevant, by=JUDGE, reviewed=False):
    return Judgement(x_id=x_id, relevant=relevant, reviewed=reviewed, labelled_by=by)


def _player(id_, fpl_id, web_name, first, second, claimed, judgements, split="dev"):
    return PlayerQuery(
        id=id_,
        split=split,
        player=PlayerInfo(
            season="2026/27",
            fpl_id=fpl_id,
            web_name=web_name,
            first_name=first,
            second_name=second,
            team="Arsenal",
            aliases=[],
        ),
        gameweek=6,
        deadline_at=T1,
        window_start=T0,
        window_end=T1,
        window_rule="alerts",
        previous_last_match_end=None,
        claimed=[ClaimedPost(x_id=x, created_at=INSIDE, stale=False) for x in claimed],
        judgements=judgements,
    )


def _free(id_, text, language, source, judgements, split="dev"):
    return FreeQuery(
        id=id_,
        text=text,
        language=language,
        origin="post",
        source_x_id=source,
        event=None,
        split=split,
        judgements=judgements,
    )


def queries():
    return [
        _player(
            "p-gw6-1",
            1,
            "Saka",
            "Bukayo",
            "Saka",
            [6],
            [_j(1, True), _j(2, True), _j(3, True), _j(4, True), _j(5, False), _j(6, True)],
        ),
        _player(
            "p-gw6-2",
            2,
            "B.Fernandes",
            "Bruno",
            "Borges Fernandes",
            [],
            [_j(7, True), _j(8, True)],
        ),
        _free("q-post-en-001", "saka injury", "en", 1, [_j(1, True), _j(2, False)]),
        _free("q-post-pl-001", "kontuzja saka", "pl", 9, [_j(9, True)]),
    ]


def eval_set(items=None, handles=("listed",)):
    return EvalSet(
        version="v2",
        manifest=SetManifest(version="v2", list_handles=list(handles) if handles else None),
        corpus=CORPUS,
        queries=items if items is not None else queries(),
        directory=Path("."),
    )


def _run(db, embedder=None, tracer=None, items=None, **kwargs):
    kwargs.setdefault("split", "dev")
    kwargs.setdefault("reference_judge", JUDGE)
    return run_evaluation(
        db,
        eval_set(items),
        embedder or FakeEmbedder(),
        tracer or RecordingTracer(),
        PRICES,
        FixedClock(),
        **kwargs,
    )


def _rows(result):
    return {(r.mode, r.variant, r.depth): r.metrics for r in result.player_rows}


def test_player_slice_searches_window_and_sources(eval_db):
    result = _run(eval_db)
    saka = result.data["queries"]["p-gw6-1"]
    assert saka["claimed"] == [6]
    for config in saka["configs"]:
        # 3 is outside the window, 4 is not a source post, 6 is claimed
        assert not {3, 4, 6} & set(config["cut"]), config
    web_name = [c for c in saka["configs"] if (c["mode"], c["variant"]) == ("fulltext", "web_name")]
    assert len(web_name) == 3
    assert all(set(c["cut"]) == {1, 2} for c in web_name)
    fulltext = _rows(result)[("fulltext", "web_name", 20)]
    # relevant and unclaimed: 1, 2, 3, 4 of which 1 and 2 are retrievable; p-gw6-2 finds nothing
    assert fulltext.queries == 2
    assert fulltext.recall_at_cut == pytest.approx((0.5 + 0.0) / 2)


def test_the_names_and_full_name_variants_find_the_initial_player(eval_db):
    result = _run(eval_db)
    rows = _rows(result)
    assert rows[("fulltext", "names", 20)].recall_at_cut == pytest.approx((0.5 + 1.0) / 2)
    assert rows[("fulltext", "full_name", 20)].recall_at_cut == pytest.approx((0.5 + 1.0) / 2)
    assert result.player_counts.with_initial == 1


def test_run_reports_every_mode_variant_and_depth_and_free_slices(eval_db):
    result = _run(eval_db)
    assert len(result.player_rows) == 3 * 3 * 3
    assert {(r.mode, r.variant, r.depth) for r in result.player_rows} == {
        (m, v, d)
        for m in ("fulltext", "vector", "hybrid")
        for v in ("web_name", "full_name", "names")
        for d in (20, 30, 50)
    }
    data = result.data
    assert len(data["player"]["rows"]) == 27
    assert set(data["free"]["modes"]) == {"fulltext", "vector", "hybrid"}
    assert set(data["free"]["modes"]["hybrid"]) == {"all", "en", "pl"}
    assert data["free"]["modes"]["fulltext"]["en"]["n"] == 1
    assert data["free"]["modes"]["fulltext"]["pl"]["unjudged_share"] is not None
    assert data["player"]["counts"]["queries"] == 2
    assert json.loads(json.dumps(data)) == data


def test_the_free_slice_reports_recall_mrr_and_the_unjudged_share(eval_db):
    result = _run(eval_db)
    fulltext = result.free.modes["fulltext"]
    # "saka injury" finds 1 first in full text
    assert fulltext["en"].mrr == 1.0
    assert fulltext["en"].recall_at_5 == 1.0
    assert 0 < fulltext["en"].unjudged_share <= 1.0
    table = format_table(result.free)
    assert "unjudged" in table.splitlines()[0]
    player_table = format_player_table(result.player_rows, result.player_counts)
    assert "recall@cut" in player_table
    assert "stale anchors 0" in player_table


def test_result_holds_cut_ids_and_labels_per_configuration(eval_db):
    data = _run(eval_db).data
    saka = data["queries"]["p-gw6-1"]
    assert len(saka["configs"]) == 27
    config = next(
        c
        for c in saka["configs"]
        if (c["mode"], c["variant"], c["depth"]) == ("fulltext", "web_name", 20)
    )
    assert sorted(config["cut"]) == [1, 2]
    assert dict(zip(config["cut"], config["cut_labels"], strict=True)) == {1: True, 2: True}
    reference = {1: True, 2: True, 3: True, 4: True, 5: False, 6: True}
    assert config["top10_labels"] == [reference.get(x) for x in config["top10"]]
    free = data["queries"]["q-post-en-001"]
    assert free["kind"] == "free"
    assert free["modes"]["fulltext"]["top10"][0] == 1
    assert free["modes"]["fulltext"]["top10_labels"][0] is True


def _record_searches(monkeypatch):
    import app.retrieval.evaluation.runner as runner

    calls = []
    real = runner.search

    def recording(engine, text, mode, **kwargs):
        calls.append((text, mode, kwargs["limit"], kwargs["depth"], kwargs["filters"]))
        return real(engine, text, mode, **kwargs)

    monkeypatch.setattr(runner, "search", recording)
    return calls


def test_depths_above_fifty_run_their_own_search(eval_db, monkeypatch):
    calls = _record_searches(monkeypatch)
    result = _run(eval_db, depths=[20, 30, 60])
    assert {r.depth for r in result.player_rows} == {20, 30, 60}
    player = [(limit, depth) for _, _, limit, depth, filters in calls if filters.sources_only]
    # 2 players x 3 variants x 3 modes, once per leg depth: 20 and 30 share leg depth 50
    assert len(player) == 2 * 3 * 3 * 2
    assert sorted(set(player)) == [(30, 50), (60, 60)]


def test_the_free_slice_is_searched_without_filters(eval_db, monkeypatch):
    calls = _record_searches(monkeypatch)
    items = [
        *queries(),
        _free("q-post-en-002", "saka rumour stranger", "en", 4, [_j(4, True)]),
    ]
    data = _run(eval_db, items=items).data
    free_calls = [call for call in calls if call[0] == "saka rumour stranger"]
    assert len(free_calls) == 3
    assert all(filters == SearchFilters() for *_, filters in free_calls)
    # 4 is by an off-list author: a search with sources_only would never return it
    assert 4 in data["queries"]["q-post-en-002"]["modes"]["fulltext"]["top10"]


def test_header_records_reference_counts_and_agreement(eval_db):
    items = queries()
    first = items[0]
    owner = [
        _j(1, True, by="owner", reviewed=True),  # agrees with the judge
        _j(2, False, by="owner", reviewed=True),  # disagrees
    ]
    versioned = [
        # 1 is owner-reviewed, so its judge label's prompt is not behind the run's labels
        j.model_copy(update={"prompt_version": {1: 9, 3: 2}[j.x_id]}) if j.x_id in (1, 3) else j
        for j in first.judgements
    ]
    items[0] = first.model_copy(update={"judgements": [*versioned, *owner]})
    free = items[2]
    items[2] = free.model_copy(
        update={"judgements": [j.model_copy(update={"prompt_version": 1}) for j in free.judgements]}
    )
    header = _run(eval_db, items=items).data["header"]
    assert header["set_version"] == "v2"
    assert header["model"] == MODEL
    assert header["judge"] == JUDGE and header["reference_judge"] == JUDGE
    assert header["prompt_version"] is None
    assert header["owner_reviewed_labels"] == 2
    assert header["judge_only_labels"] == 9
    assert header["judge_owner_agreement"] == pytest.approx(0.5)
    assert header["split"] == "dev" and header["depths"] == [20, 30, 50]
    assert header["relevance_prompts"] == {"player": [2], "free": [1]}
    assert header["estimated_embedding_tokens"] > 0
    # the owner's label beats the judge's: post 2 is no longer relevant
    data = _run(eval_db, items=items).data
    assert data["queries"]["p-gw6-1"]["relevant"] == [1, 3, 4, 6]


def test_no_reference_label_refuses_before_any_embedding(eval_db):
    other = [
        q.model_copy(
            update={"judgements": [j.model_copy(update={"labelled_by": "b"}) for j in q.judgements]}
        )
        for q in queries()
    ]
    embedder = FakeEmbedder()
    with pytest.raises(EvaluationError, match="no label from judge-a or the owner"):
        _run(eval_db, embedder=embedder, items=other)
    with pytest.raises(EvaluationError, match="in the test split"):
        _run(eval_db, embedder=embedder, split="test")
    assert embedder.calls == []


def test_estimate_is_reported_and_the_cap_refuses_before_embedding(eval_db):
    seen = []
    embedder = FakeEmbedder()
    with pytest.raises(EvaluationError, match="above --max-cost-usd"):
        _run(eval_db, embedder=embedder, max_cost_usd=1e-9, on_estimate=seen.append)
    assert embedder.calls == []
    assert len(seen) == 1 and seen[0].tokens > 0 and seen[0].cost_usd > 1e-9


def test_model_without_price_row_refused(eval_db):
    embedder = FakeEmbedder(model="no/price")
    with pytest.raises(EvaluationError, match="no/price has no row in prices.toml"):
        _run(eval_db, embedder=embedder)
    assert embedder.calls == []


def _write_set(tmp_path, items=None):
    write_corpus(tmp_path / "corpus.jsonl", CORPUS)
    write_manifest(tmp_path, SetManifest(version="v2", list_handles=["listed"]))
    write_queries(tmp_path / "queries.jsonl", items if items is not None else queries())


def _evaluate(eval_db, tmp_path, *args, embedder=None, deps=None):
    return CliRunner().invoke(
        app,
        [
            "evaluate",
            "--split",
            "dev",
            "--reference-judge",
            JUDGE,
            "--set",
            str(tmp_path),
            "--output",
            str(tmp_path / "out" / "run.json"),
            *args,
        ],
        obj=deps or _deps(eval_db, {MODEL: embedder or FakeEmbedder()}),
    )


def test_evaluate_prints_the_estimate_and_both_tables(eval_db, tmp_path):
    _write_set(tmp_path)
    result = _evaluate(eval_db, tmp_path)
    assert result.exit_code == 0, result.output
    assert "estimated embedding:" in result.stdout
    assert "recall@cut" in result.stdout and "recall@5" in result.stdout
    written = json.loads((tmp_path / "out" / "run.json").read_text())
    assert written["header"]["set_version"] == "v2"


def test_estimate_cap_refuses_through_the_cli(eval_db, tmp_path):
    _write_set(tmp_path)
    embedder = FakeEmbedder()
    result = _evaluate(eval_db, tmp_path, "--max-cost-usd", "0.0000001", embedder=embedder)
    assert result.exit_code == 1
    assert "estimated embedding:" in result.stdout
    assert "above --max-cost-usd" in result.stderr
    assert embedder.calls == []
    assert not (tmp_path / "out").exists()


def test_a_model_without_a_price_row_is_refused_by_the_cli(eval_db, tmp_path):
    _write_set(tmp_path)
    settings = RetrievalSettings(_env_file=None, openrouter_api_key="k")
    base = _deps(eval_db)
    deps = RetrievalCliDeps(
        **{**base.__dict__, "make_embedder": embedder_from_settings(settings), "prices": None}
    )
    result = _evaluate(eval_db, tmp_path, "--embedding-model", "x/unpriced", deps=deps)
    assert result.exit_code == 1
    assert "no row in prices.toml" in result.stderr


def test_evaluate_writes_header_and_guard_refuses(eval_db, tmp_path):
    _write_set(tmp_path)
    first = _evaluate(eval_db, tmp_path)
    assert first.exit_code == 0, first.output
    path = tmp_path / "out" / "run.json"
    header = json.loads(path.read_text())["header"]
    assert header["model"] == MODEL
    assert header["date"] and "git_commit" in header and "git_dirty" in header

    embedder = FakeEmbedder()
    again = _evaluate(eval_db, tmp_path, embedder=embedder)
    assert again.exit_code == 1
    assert str(path) in again.stderr and "--force" in again.stderr
    assert embedder.calls == []
    forced = _evaluate(eval_db, tmp_path, "--force")
    assert forced.exit_code == 0, forced.output


def test_evaluate_rejects_bad_depths_and_split(eval_db, tmp_path):
    _write_set(tmp_path)
    assert _evaluate(eval_db, tmp_path, "--depths", "20,x").exit_code == 1
    assert _evaluate(eval_db, tmp_path, "--depths", "0").exit_code == 1


def test_unembeddable_corpus_stops_without_file(eval_db, tmp_path):
    _write_set(tmp_path)
    deps = _deps(eval_db, {MODEL: AlwaysFailingEmbedder()})
    result = _evaluate(eval_db, tmp_path, deps=deps)
    assert result.exit_code == 1
    assert f"error: {len(CORPUS)} corpus posts could not be embedded" in result.stderr
    assert not (tmp_path / "out").exists()


def test_a_query_that_cannot_be_embedded_is_named(eval_db):
    embedder = FakeEmbedder()
    embedder.responses = [None] * len(CORPUS) + [RuntimeError("x")] * 3
    with pytest.raises(EvaluationError, match="could not be embedded: RuntimeError"):
        _run(eval_db, embedder=embedder)


def test_runner_traces_query_embeddings_and_searches_but_not_the_corpus(eval_db):
    tracer = RecordingTracer()
    _run(eval_db, tracer=tracer)
    texts = {r["texts"][0] for r in tracer.embeddings}
    assert {"saka injury", "kontuzja saka", "Saka", "Bruno Borges Fernandes", "Fernandes"} <= texts
    assert len(tracer.embeddings) == len(texts)
    assert not texts & {p.text for p in POSTS}
    # per player query: 3 variants x 3 modes (one leg group); per free query: 3 modes
    assert len(tracer.searches) == 2 * 9 + 2 * 3
    assert tracer.flushed >= 1


def test_default_run_name():
    assert default_run_name("dev", "v2", "openai/text-embedding-3-small") == (
        "dev-v2-openai-text-embedding-3-small"
    )
