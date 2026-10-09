import json

from app.presser.evaluation.runner import compute_totals
from app.presser.evaluation.summary import (
    FAITHFULNESS_THRESHOLD,
    STYLE_THRESHOLD,
    choose,
    summarise,
    summarise_run,
)


def presser(n, faithfulness=1.0, rating=4, cost=0.001, error=None, within=True):
    return {
        "case_id": f"c{n}",
        "text": "t",
        "length": 1,
        "within_limit": within,
        "input_tokens": 1,
        "output_tokens": 1,
        "cost_usd": cost,
        "latency_seconds": 2.0,
        "claims": [],
        "faithfulness": faithfulness,
        "judge_cost_usd": 0.01,
        "error_class": error,
        "style": None if rating is None else {"rating": rating, "note": None},
    }


def run(model, pressers, split="test"):
    return {
        "model": model,
        "judge_model": "j",
        "split": split,
        "pressers": pressers,
        "totals": compute_totals(pressers) | {"judge_agreement": 0.9},
    }


def write(directory, data):
    (directory / f"{data['split']}-{data['model'].replace('/', '-')}.json").write_text(
        json.dumps(data), encoding="utf-8"
    )


def test_thresholds():
    assert (FAITHFULNESS_THRESHOLD, STYLE_THRESHOLD) == (0.95, 3.5)


def test_cheapest_passing_model_wins(tmp_path):
    cheap = [presser(n, rating=r, cost=0.0005) for n, r in enumerate([3, 3, 3, 4, 4])]
    write(tmp_path, run("cheap/model", cheap))
    write(tmp_path, run("mid/model", [presser(n, cost=0.002) for n in range(4)]))
    write(tmp_path, run("rich/model", [presser(n, cost=0.01) for n in range(4)]))
    write(tmp_path, run("dev/model", [presser(n, cost=0.0001) for n in range(4)], "dev"))
    runs = summarise(tmp_path)
    assert [r.model for r in runs] == ["cheap/model", "mid/model", "rich/model"]
    assert runs[0].style == 3.4 and not runs[0].passes
    assert [r.passes for r in runs[1:]] == [True, True]
    assert choose(runs) == "mid/model"


def test_a_cheap_model_failing_on_style_loses():
    cheap = summarise_run(run("cheap/model", [presser(n, rating=3, cost=0.0001) for n in range(4)]))
    mid = summarise_run(run("mid/model", [presser(n, cost=0.002) for n in range(4)]))
    assert not cheap.passes and cheap.style == 3.0
    assert choose([cheap, mid]) == "mid/model"


def test_unrated_or_errored_does_not_pass():
    unrated = summarise_run(run("a/m", [presser(1), presser(2, rating=None)]))
    errored = summarise_run(run("b/m", [presser(1), presser(2, faithfulness=None, error="X")]))
    assert not unrated.passes and unrated.rated == 1
    assert not errored.passes and errored.errored == 1
    assert choose([unrated, errored]) is None


def test_thresholds_inclusive():
    exact = [presser(n, faithfulness=1.0, rating=4 if n % 2 else 3) for n in range(19)]
    exact.append(presser(19, faithfulness=0.0, rating=4))
    summary = summarise_run(run("a/m", exact))
    assert summary.faithfulness == 0.95
    assert summary.style >= 3.5
    assert summary.passes
    below = summarise_run(run("b/m", [presser(n, faithfulness=0.9) for n in range(4)]))
    assert not below.passes


def test_tie_goes_to_the_higher_faithfulness():
    first = summarise_run(run("a/m", [presser(n, faithfulness=1.0) for n in range(4)]))
    second = summarise_run(run("b/m", [presser(n, faithfulness=0.97) for n in range(4)]))
    assert choose([second, first]) == "a/m"


def test_unknown_cost_cannot_be_chosen():
    unknown = summarise_run(run("a/m", [presser(n, cost=None) for n in range(4)]))
    assert unknown.passes
    assert choose([unknown]) is None


def test_summary_fields():
    summary = summarise_run(run("a/m", [presser(1, within=False), presser(2)]))
    assert summary.within_limit_share == 0.5
    assert summary.avg_latency_seconds == 2.0
    assert summary.judge_agreement == 0.9
    assert summary.pressers == 2
