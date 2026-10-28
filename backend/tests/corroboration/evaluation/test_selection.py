from app.corroboration.evaluation.selection import choose_judge


def run(model, cost, *, accuracy=True, false_support=True, recall=True, errors=True, fs_rate=0.05):
    verdict = {
        "accuracy": accuracy,
        "false_support": false_support,
        "contradicts_recall": recall,
        "no_errors": errors,
    }
    verdict["passed"] = all(verdict.values())
    return {
        "model": model,
        "verdict": verdict,
        "metrics": {"false_support": {"rate": fs_rate}},
        "projected_monthly_cost_pln": cost,
    }


def _runs(*items):
    return {f"test-{d['model']}": d for d in items}


def test_cheapest_passing_wins():
    runs = _runs(run("a", 3.0), run("b", 1.0), run("c", 0.5, accuracy=False))
    assert choose_judge(runs) == "b"


def test_no_pass_most_thresholds_then_lower_false_support_then_cheaper():
    runs = _runs(
        run("a", 1.0, accuracy=False, recall=False),
        run("b", 2.0, accuracy=False, fs_rate=0.12),
        run("c", 3.0, accuracy=False, fs_rate=0.08),
    )
    # b and c fail one threshold each; c has the lower false-support rate
    assert choose_judge(runs) == "c"
    tie = _runs(
        run("a", 3.0, accuracy=False, fs_rate=0.08),
        run("b", 2.0, accuracy=False, fs_rate=0.08),
    )
    assert choose_judge(tie) == "b"


def test_no_pass_excludes_runs_over_5_pln_or_without_a_cost():
    runs = _runs(
        run("a", 6.0, accuracy=False),
        run("b", None, accuracy=False),
        run("c", 4.0, accuracy=False, recall=False),
    )
    assert choose_judge(runs) == "c"


def test_repeat_replaces_the_first_run():
    first = run("a", 1.0, errors=False)
    repeat = run("a", 1.2)
    runs = {"test-a": first, "test-a-r2": repeat, "test-b": run("b", 2.0)}
    assert choose_judge(runs) == "a"
    assert choose_judge({"test-a": first, "test-b": run("b", 2.0)}) == "b"


def test_no_eligible_run_gives_none():
    assert choose_judge({}) is None
    assert choose_judge(_runs(run("a", 9.0, accuracy=False))) is None
