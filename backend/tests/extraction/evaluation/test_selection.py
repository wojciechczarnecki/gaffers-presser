import json

import pytest

from app.extraction.evaluation.selection import (
    RunSummary,
    Selection,
    select_models,
    summaries_from_results,
)

NAMES = ("f1", "linking_accuracy", "false_alarm_rate", "monthly_cost", "no_errored_cases")


def run(model, f1=0.9, cost=1.0, failed=()):
    flags = {name: name not in failed for name in NAMES}
    if cost is None or cost > 5.0:
        flags["monthly_cost"] = False
    return RunSummary(
        model=model,
        run_name=f"test-p3-{model}",
        passes=all(flags.values()),
        thresholds_passed=flags,
        f1=f1,
        monthly_cost_pln=cost,
    )


def test_cheapest_passing_is_default_second_cheapest_fallback():
    runs = [
        run("dear", cost=3.0),
        run("cheap", cost=0.5),
        run("mid", cost=1.0),
        run("bad", 0.5, 0.1, ("f1",)),
    ]
    assert select_models(runs) == Selection("cheap", "mid", False, ())


def test_cost_ties_break_by_higher_f1():
    runs = [run("a", f1=0.90, cost=1.0), run("b", f1=0.95, cost=1.0)]
    assert select_models(runs).default == "b"


def test_single_passing_fallback_best_within_budget():
    runs = [
        run("winner", cost=2.0),
        run("close", f1=0.8, cost=1.0, failed=("f1",)),
        run("worse", f1=0.7, cost=0.5, failed=("f1", "linking_accuracy")),
        run("dear", f1=0.99, cost=9.0, failed=("monthly_cost",)),
    ]
    assert select_models(runs) == Selection("winner", "close", False, ())


def test_single_passing_no_fallback_when_none_within_budget():
    runs = [run("winner", cost=2.0), run("dear", f1=0.99, cost=9.0)]
    assert select_models(runs) == Selection("winner", None, False, ())


def test_no_passing_interim_default_and_missed_thresholds():
    runs = [
        run("a", f1=0.8, cost=1.0, failed=("f1",)),
        run("b", f1=0.7, cost=0.5, failed=("f1", "false_alarm_rate")),
        run("dear", f1=0.99, cost=9.0),
    ]
    selection = select_models(runs)
    assert selection == Selection("a", "b", True, ("f1",))


def test_best_orders_by_thresholds_then_f1():
    runs = [
        run("many-fail-high-f1", f1=0.99, failed=("linking_accuracy", "false_alarm_rate")),
        run("one-fail-low-f1", f1=0.5, failed=("f1",)),
        run("one-fail-higher-f1", f1=0.6, failed=("f1",)),
    ]
    selection = select_models(runs)
    assert (selection.default, selection.fallback) == ("one-fail-higher-f1", "one-fail-low-f1")


def test_fallback_skips_models_incompatible_with_the_default():
    runs = [
        run("winner", cost=2.0),
        run("best-but-incompatible", f1=0.95, cost=1.0, failed=("false_alarm_rate",)),
        run("usable", f1=0.8, cost=1.5, failed=("f1", "false_alarm_rate")),
    ]

    def compatible(primary, fallback):
        return fallback != "best-but-incompatible"

    assert select_models(runs, compatible) == Selection("winner", "usable", False, ())
    assert select_models(runs).fallback == "best-but-incompatible"


def test_second_passing_incompatible_falls_back_to_best_compatible_other():
    runs = [
        run("cheap", cost=0.5),
        run("mid", cost=1.0),
        run("other", f1=0.8, cost=2.0, failed=("f1",)),
    ]
    selection = select_models(runs, lambda primary, fallback: fallback != "mid")
    assert selection == Selection("cheap", "other", False, ())


def test_interim_pair_respects_compatibility():
    runs = [
        run("a", f1=0.8, cost=1.0, failed=("f1",)),
        run("b", f1=0.79, cost=0.5, failed=("f1",)),
        run("c", f1=0.7, cost=0.5, failed=("f1", "false_alarm_rate")),
    ]
    selection = select_models(runs, lambda primary, fallback: fallback != "b")
    assert (selection.default, selection.fallback, selection.interim) == ("a", "c", True)


def test_no_compatible_fallback_gives_none():
    runs = [run("winner", cost=2.0), run("close", f1=0.8, cost=1.0, failed=("f1",))]
    assert select_models(runs, lambda primary, fallback: False).fallback is None


def test_none_within_budget_raises():
    with pytest.raises(ValueError, match="5"):
        select_models([run("dear", cost=9.0), run("unknown", cost=None)])


def _write(directory, name, model, split, f1, cost, passes, flags=None):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.json").write_text(
        json.dumps(
            {
                "run_name": name,
                "model": model,
                "split": split,
                "metrics": {
                    "f1": f1,
                    "projected_monthly_cost_pln": cost,
                    "passes": passes,
                    "thresholds_passed": flags or {n: passes for n in NAMES},
                },
            }
        )
    )


def test_rerun_replaces_first_run(tmp_path):
    _write(tmp_path, "test-p3-m", "a/m", "test", 0.5, 1.0, False)
    _write(tmp_path, "test-p3-m-r2", "a/m", "test", 0.9, 1.0, True)
    _write(tmp_path, "test-p3-n", "a/n", "test", 0.7, 2.0, False)

    summaries = {s.model: s for s in summaries_from_results(tmp_path)}

    assert set(summaries) == {"a/m", "a/n"}
    assert summaries["a/m"].run_name == "test-p3-m-r2"
    assert summaries["a/m"].passes is True
    assert summaries["a/m"].monthly_cost_pln == 1.0


def test_only_test_split_files_directly_in_the_directory(tmp_path):
    _write(tmp_path, "test-p3-m", "a/m", "test", 0.9, 1.0, True)
    _write(tmp_path, "dev-p3-m", "a/m", "dev", 0.9, 1.0, True)
    _write(tmp_path / "dev", "dev-p3-x", "a/x", "dev", 0.9, 1.0, True)

    assert [s.run_name for s in summaries_from_results(tmp_path)] == ["test-p3-m"]
