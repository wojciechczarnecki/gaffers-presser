from datetime import datetime, timedelta

import pytest
from typer.testing import CliRunner

from app.corroboration.evaluation.building import (
    BuildExistsError,
    assign_split,
    build_candidates,
    build_cases,
    build_window_candidates,
    prelabel,
    sample_candidates,
    select_cases,
    write_candidate_files,
)
from app.corroboration.evaluation.cases import (
    LABELS,
    CandidateCase,
    is_known_miss,
    load_candidates,
    load_cases,
)
from app.corroboration.evaluation.dataset import BuildWindow, load_manifest
from app.corroboration.sources import MAX_JUDGED
from tests.corroboration.evaluation.fakes import ScriptedJudge
from tests.corroboration.evaluation.test_cases import make_case
from tests.corroboration.helpers import ISAK, NOW, SAKA, add_claim, add_fixture, seed_reference
from tests.retrieval.fakes import FakeEmbedder
from tests.retrieval.helpers import PRICES, add_tweet


def _corpus(db) -> None:
    seed_reference(db)
    add_claim(db, 1, SAKA, "confirmed_starter", created_at=NOW - timedelta(hours=9), author="a1")
    add_claim(db, 2, SAKA, "doubt", created_at=NOW - timedelta(hours=8), author="a2")
    add_claim(db, 3, SAKA, "out", created_at=NOW - timedelta(hours=7), author="a3")
    add_claim(
        db, 4, ISAK, "doubt", created_at=NOW - timedelta(hours=6), author="a4", mention="Isak"
    )
    add_tweet(db, 10, "Saka injury news", created_at=NOW - timedelta(hours=5), author="m1")
    add_tweet(db, 11, "Saka trains today", created_at=NOW - timedelta(hours=4), author="m2")
    add_tweet(db, 12, "Isak withdrawn injury", created_at=NOW - timedelta(hours=3), author="m3")
    add_tweet(
        db, 13, "Unrelated football chatter", created_at=NOW - timedelta(hours=2), author="m4"
    )


def test_candidates_come_per_player_and_anchor_with_the_anchor_post_excluded(db):
    _corpus(db)
    candidates = build_candidates(db, FakeEmbedder(), PRICES)
    pairs = {(c.player.fpl_id, c.anchor.post.x_id) for c in candidates}
    # Saka: the newest claim (3, out) and the newest claim of another type (2, doubt);
    # Isak has one claim only, so one anchor.
    assert pairs == {(SAKA, 3), (SAKA, 2), (ISAK, 4)}
    assert all(c.post.x_id != c.anchor.post.x_id for c in candidates)


def test_has_player_event_marks_sql_claims(db):
    _corpus(db)
    candidates = build_candidates(db, FakeEmbedder(), PRICES)
    saka = [c for c in candidates if c.player.fpl_id == SAKA and c.anchor.post.x_id == 3]
    with_event = {c.post.x_id for c in saka if c.has_player_event}
    without = {c.post.x_id for c in saka if not c.has_player_event}
    assert with_event == {1, 2}
    assert without == {10, 11}
    assert 13 not in without


def test_prelabels_come_from_the_injected_judge_and_are_unreviewed(db):
    _corpus(db)
    judge = ScriptedJudge(label=lambda item: "related", model="pre/labeller")
    result = prelabel(build_candidates(db, FakeEmbedder(), PRICES), judge, "pre/labeller")
    cases = result.cases
    assert cases
    assert result.cost_usd == pytest.approx(0.001 * len(cases))
    assert {c.labelled_by for c in cases} == {"pre/labeller"}
    assert {c.reviewed for c in cases} == {False}
    assert {c.expected for c in cases} == {"related"}
    assert len({c.id for c in cases}) == len(cases)


def test_a_failed_prelabel_is_skipped(db):
    _corpus(db)
    judge = ScriptedJudge(label=lambda item: RuntimeError("down"))
    result = prelabel(build_candidates(db, FakeEmbedder(), PRICES), judge, "m")
    assert result.cases == []
    assert result.failed > 0
    assert result.cost_usd is None


def _pool():
    cases = []
    n = 0
    for label, count in (("supports", 30), ("contradicts", 8), ("related", 20), ("unrelated", 60)):
        for i in range(count):
            n += 1
            cases.append(make_case(n, label, has_player_event=i % 3 != 0))
    return cases


def test_selection_is_deterministic_for_a_seed():
    first = select_cases(_pool(), size=60, seed=3)
    second = select_cases(_pool(), size=60, seed=3)
    other = select_cases(_pool(), size=60, seed=4)
    assert [c.id for c in first] == [c.id for c in second]
    assert [c.id for c in first] != [c.id for c in other]
    assert len(first) == 60


def test_selection_caps_unrelated_and_keeps_every_label():
    chosen = select_cases(_pool(), size=60, seed=3)
    counts = {label: sum(1 for c in chosen if c.expected == label) for label in LABELS}
    assert counts["unrelated"] <= 24
    assert all(counts[label] > 0 for label in LABELS)
    assert counts["contradicts"] == 8


def test_selection_puts_known_misses_first_within_a_label():
    pool = _pool()
    misses = {c.id for c in pool if c.expected == "supports" and is_known_miss(c)}
    chosen = select_cases(pool, size=24, seed=3)
    picked_supports = [c for c in chosen if c.expected == "supports"]
    assert len(picked_supports) == 6
    assert all(c.id in misses for c in picked_supports)


def test_the_split_is_stratified_by_label():
    cases = assign_split(select_cases(_pool(), size=60, seed=3), 0.3, seed=3)
    for label in LABELS:
        of_label = [c for c in cases if c.expected == label]
        dev = sum(1 for c in of_label if c.split == "dev")
        assert dev == min(round(0.3 * len(of_label)), len(of_label) - 1)
        assert any(c.split == "test" for c in of_label)


def test_build_cases_end_to_end(db):
    _corpus(db)
    labels = iter(["supports", "contradicts", "related", "unrelated"] * 10)
    judge = ScriptedJudge(label=lambda item: next(labels), model="pre/labeller")
    built = build_cases(db, FakeEmbedder(), judge, size=8, seed=1, prices=PRICES)
    cases = built.cases
    assert 0 < len(cases) <= 8
    assert built.prelabelled == built.candidates
    assert {c.labelled_by for c in cases} == {"pre/labeller"}
    assert {c.split for c in cases} <= {"dev", "test"}


def _deps(db, judge=None):
    from app.corroboration.config import CorroborationSettings
    from app.corroboration.evaluation.cli import EvaluationCliDeps
    from tests.retrieval.helpers import FixedClock

    judge = judge or ScriptedJudge(label=lambda item: "supports", model="pre/labeller")
    return EvaluationCliDeps(
        engine=db,
        settings=CorroborationSettings(_env_file=None),
        clock=FixedClock(NOW),
        make_embedder=lambda: FakeEmbedder(),
        make_judge=lambda model: judge,
        prices=PRICES,
    )


def test_build_cases_command_writes_the_file_and_prints_counts_and_cost(db, tmp_path):
    from app.corroboration.evaluation.cli import app

    _corpus(db)
    output = tmp_path / "cases.jsonl"
    result = CliRunner().invoke(
        app, ["build-cases", "--output", str(output), "--size", "8"], obj=_deps(db)
    )
    assert result.exit_code == 0, result.output
    cases = load_cases(output)
    assert cases and all(not c.reviewed for c in cases)
    assert "cost: $" in result.stdout
    assert "labels: supports" in result.stdout
    assert f"cases: {len(cases)}" in result.stdout
    assert "pre-labelled by: anthropic/claude-haiku-4.5" in result.stdout


def test_build_cases_refuses_to_overwrite_without_force(db, tmp_path):
    from app.corroboration.evaluation.cli import app

    _corpus(db)
    output = tmp_path / "cases.jsonl"
    output.write_text("keep me\n")
    result = CliRunner().invoke(app, ["build-cases", "--output", str(output)], obj=_deps(db))
    assert result.exit_code == 1
    assert "--force" in result.output
    assert output.read_text() == "keep me\n"
    forced = CliRunner().invoke(
        app, ["build-cases", "--output", str(output), "--force", "--size", "8"], obj=_deps(db)
    )
    assert forced.exit_code == 0, forced.output
    assert output.read_text() != "keep me\n"


def test_prelabel_model_is_catalogued_and_not_default():
    from app.corroboration.evaluation.cases import PRELABEL_MODEL
    from app.llm.chat import DEFAULT_MODEL
    from app.llm.models import load_model_settings
    from app.llm.pricing import load_prices

    assert PRELABEL_MODEL in load_model_settings()
    assert PRELABEL_MODEL in load_prices()
    assert PRELABEL_MODEL != DEFAULT_MODEL


def test_a_stale_lineup_is_neither_an_anchor_nor_a_candidate(db):
    _corpus(db)
    add_fixture(db, 1, None, NOW + timedelta(days=4))
    add_claim(
        db,
        5,
        SAKA,
        "benched",
        "confirmed",
        created_at=NOW - timedelta(hours=1),
        author="a5",
        text="Saka on the bench tonight",
    )
    candidates = build_candidates(db, FakeEmbedder(), PRICES)
    assert {(c.player.fpl_id, c.anchor.post.x_id) for c in candidates} == {
        (SAKA, 3),
        (SAKA, 2),
        (ISAK, 4),
    }
    assert all(c.post.x_id != 5 for c in candidates)


START = NOW - timedelta(hours=24)
WINDOW = BuildWindow(gameweek=6, start=START, end=NOW)


def _window_corpus(db) -> None:
    seed_reference(db, {7: NOW + timedelta(days=3, hours=22)})
    add_fixture(db, 1, 7, NOW + timedelta(days=4))
    # a stale confirmed line-up: neither an anchor nor a retrieval candidate
    add_claim(
        db,
        1,
        SAKA,
        "confirmed_starter",
        "confirmed",
        created_at=NOW - timedelta(hours=9),
        author="a1",
    )
    add_claim(db, 2, SAKA, "doubt", created_at=NOW - timedelta(hours=8), author="a2")
    for n in range(14):
        add_tweet(
            db, 100 + n, f"Saka injury news {n}", created_at=NOW - timedelta(hours=5, minutes=n)
        )
    add_tweet(db, 200, "Saka injury news before the window", created_at=START - timedelta(hours=1))


def test_candidates_use_the_production_retrieval_and_fresh_lineups(db):
    _window_corpus(db)
    built = build_window_candidates(db, FakeEmbedder(), [WINDOW], PRICES)
    anchors = {c.anchor.x_id for c in built.candidates}
    assert anchors == {2}
    retrieved = [c for c in built.candidates if not c.has_player_event]
    assert 0 < len(retrieved) <= MAX_JUDGED
    assert all(c.post.x_id not in {1, 2} for c in retrieved)


def test_candidates_inside_their_window(db):
    _window_corpus(db)
    built = build_window_candidates(db, FakeEmbedder(), [WINDOW], PRICES)
    for c in built.candidates:
        assert START <= c.anchor.created_at < NOW
        assert START <= c.post.created_at < NOW
    assert 200 not in {c.post.x_id for c in built.candidates}


def test_sql_pairs_contradicts_first(db):
    seed_reference(db)
    add_claim(db, 1, SAKA, "confirmed_starter", created_at=NOW - timedelta(hours=1), author="a1")
    add_claim(db, 2, SAKA, "out", created_at=NOW - timedelta(hours=9), author="a2")
    add_claim(db, 3, SAKA, "doubt", created_at=NOW - timedelta(hours=8), author="a3")
    for n in (4, 5, 6):
        add_claim(
            db, n, SAKA, "confirmed_starter", created_at=NOW - timedelta(hours=n), author=f"a{n}"
        )
    built = build_window_candidates(db, FakeEmbedder(), [WINDOW], PRICES)
    pairs = [c for c in built.candidates if c.anchor.x_id == 1 and c.has_player_event]
    assert len(pairs) == 3
    assert {c.post.x_id for c in pairs if c.tier == "sql-contradicts"} == {2, 3}
    assert {c.tier for c in pairs} == {"sql-contradicts", "sql"}


def _cand(n, tier, has_event=False):
    case = make_case(n, has_player_event=has_event)
    return CandidateCase(
        id=f"c-{n:04d}",
        player=case.player,
        anchor=case.anchor,
        post=case.post,
        has_player_event=has_event,
        tier=tier,
    )


def test_tiers_and_sampling_deterministic():
    pool = (
        [_cand(i, "sql-contradicts", True) for i in range(5)]
        + [_cand(100 + i, "retrieved-likely") for i in range(20)]
        + [_cand(200 + i, "retrieved") for i in range(50)]
        + [_cand(300 + i, "sql", True) for i in range(30)]
    )
    first = sample_candidates(pool, max_inputs=40, seed=3, likely_inputs=10)
    assert first == sample_candidates(pool, max_inputs=40, seed=3, likely_inputs=10)
    assert first != sample_candidates(pool, max_inputs=40, seed=4, likely_inputs=10)
    assert len(first) == 40
    tiers = [c.tier for c in first]
    assert tiers.count("sql-contradicts") == 5
    assert tiers.count("retrieved-likely") == 10
    assert tiers.count("retrieved") == 25
    assert tiers.count("sql") == 0


def test_volume_counts_newest_anchor_candidates_times_slots(db):
    seed_reference(db)
    add_claim(db, 1, SAKA, "doubt", created_at=NOW - timedelta(hours=8), author="a1")
    add_claim(db, 2, SAKA, "confirmed_starter", created_at=NOW - timedelta(hours=7), author="a2")
    for n in range(3):
        add_tweet(db, 100 + n, f"Saka injury news {n}", created_at=NOW - timedelta(hours=3))
    built = build_window_candidates(db, FakeEmbedder(), [WINDOW], PRICES)
    newest = [c for c in built.candidates if c.anchor.x_id == 2 and not c.has_player_event]
    assert len(newest) == 3
    assert built.judge_calls_per_month == 3 * 2 * 4


def _index(db):
    import threading

    from app.retrieval.indexing import IndexingRuntime, index_missing
    from app.retrieval.tracing import NULL_TRACER
    from tests.retrieval.helpers import FixedClock

    embedder = FakeEmbedder()
    runtime = IndexingRuntime(
        model=embedder.model,
        make_embedder=lambda: embedder,
        prices=PRICES,
        tracing=None,
        clock=FixedClock(),
    )
    index_missing(db, runtime, embedder, NULL_TRACER, FixedClock(), threading.Event())


def _files(db, tmp_path, **kwargs):
    _index(db)
    set_dir = tmp_path / "v2"
    return write_candidate_files(
        db,
        FakeEmbedder(),
        PRICES,
        windows=[WINDOW],
        set_dir=set_dir,
        out=tmp_path / "candidates.jsonl",
        list_handles=["a1", "a2"],
        corpus="evals/retrieval/v2/corpus.jsonl",
        **kwargs,
    )


def test_build_candidates_writes_the_build_section(db, tmp_path):
    _window_corpus(db)
    count, info = _files(db, tmp_path)
    manifest = load_manifest(tmp_path / "v2")
    assert manifest.build == info
    assert manifest.list_handles == ["a1", "a2"]
    assert info.windows == [WINDOW]
    assert info.extraction_model == "fake-model"
    assert info.extraction_prompt_version.startswith("extraction@")
    assert info.query_embeddings_cost_usd > 0
    assert info.judge_calls_per_month > 0
    assert len(load_candidates(tmp_path / "candidates.jsonl")) == count


def test_build_candidates_refuses_an_existing_build_without_force(db, tmp_path):
    _window_corpus(db)
    _files(db, tmp_path)
    with pytest.raises(BuildExistsError):
        _files(db, tmp_path)


def test_build_candidates_force_adds_the_query_cost(db, tmp_path):
    _window_corpus(db)
    _, first = _files(db, tmp_path)
    _, second = _files(db, tmp_path, force=True, seed=20)
    assert second.windows == first.windows
    assert second.judge_calls_per_month == first.judge_calls_per_month
    assert second.query_embeddings_cost_usd == pytest.approx(2 * first.query_embeddings_cost_usd)


def test_a_datetime_import_is_used():
    assert isinstance(START, datetime)


def test_a_claim_from_outside_the_list_is_not_an_anchor(db):
    from tests.tweets.membership_helpers import set_members

    seed_reference(db)
    add_claim(db, 1, SAKA, "doubt", created_at=NOW - timedelta(hours=8), author="member")
    add_claim(db, 2, SAKA, "out", created_at=NOW - timedelta(hours=2), author="stranger")
    add_tweet(db, 3, "Saka injury news", created_at=NOW - timedelta(hours=3), author="member")
    set_members(db, ["member"])
    built = build_window_candidates(db, FakeEmbedder(), [WINDOW], PRICES)
    assert {c.anchor.x_id for c in built.candidates} == {1}
    assert 2 not in {c.post.x_id for c in built.candidates}
