from datetime import timedelta

import pytest
from typer.testing import CliRunner

from app.corroboration.evaluation.building import (
    assign_split,
    build_candidates,
    build_cases,
    prelabel,
    select_cases,
)
from app.corroboration.evaluation.cases import LABELS, is_known_miss, load_cases
from tests.corroboration.evaluation.fakes import ScriptedJudge
from tests.corroboration.evaluation.test_cases import make_case
from tests.corroboration.helpers import ISAK, NOW, SAKA, add_claim, seed_reference
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
