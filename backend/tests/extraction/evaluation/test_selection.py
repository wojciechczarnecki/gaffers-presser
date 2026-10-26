from app.extraction.evaluation.selection import Selection, choose_models
from app.llm.models import ModelSettings

CATALOGUE = {
    name: ModelSettings("none", True, method, "2026-10-26")
    for name, method in {
        "a/cheap": "function_calling",
        "a/pricey": "function_calling",
        "a/other": "function_calling",
        "a/json": "json_schema",
        "a/over": "function_calling",
    }.items()
}


def run(model, f1=0.9, linking=0.98, false_alarms=0.03, pln=1.0, errored=0, name=None):
    return {
        "model": model,
        "run_name": name or f"test-v2-{model}",
        "metrics": {
            "f1": f1,
            "linking_accuracy": linking,
            "false_alarm_rate": false_alarms,
            "projected_monthly_cost_pln": pln,
            "errored_cases": errored,
        },
    }


def test_the_cheapest_passing_model_wins():
    runs = [run("a/pricey", pln=3.0), run("a/cheap", pln=0.5), run("a/other", pln=1.0)]
    assert choose_models(runs, CATALOGUE) == Selection("a/cheap", "a/other")


def test_with_none_passing_the_most_thresholds_then_f1_wins():
    runs = [
        run("a/cheap", f1=0.8, false_alarms=0.09),
        run("a/pricey", f1=0.8, pln=2.0),
        run("a/other", f1=0.84, pln=2.0),
    ]
    assert choose_models(runs, CATALOGUE).default == "a/other"


def test_a_model_over_five_pln_never_wins():
    runs = [run("a/over", f1=0.99, pln=8.0), run("a/cheap", f1=0.5, linking=0.5, pln=1.0)]
    assert choose_models(runs, CATALOGUE).default == "a/cheap"


def test_the_fallback_is_pair_compatible_with_the_winner():
    runs = [run("a/cheap", pln=0.5), run("a/json", pln=0.6), run("a/other", false_alarms=0.09)]
    assert choose_models(runs, CATALOGUE) == Selection("a/cheap", "a/other")


def test_with_no_compatible_fallback_there_is_none():
    runs = [run("a/cheap", pln=0.5), run("a/json", pln=0.6)]
    assert choose_models(runs, CATALOGUE) == Selection("a/cheap", None)


def test_a_repeat_run_supersedes_the_first_run_of_a_model():
    runs = [
        run("a/cheap", errored=2, pln=0.5),
        run("a/cheap", pln=0.5, name="test-v2-a/cheap-r2"),
        run("a/other", pln=1.0),
    ]
    assert choose_models(runs, CATALOGUE).default == "a/cheap"


def test_no_model_within_the_cost_limit_gives_none():
    assert choose_models([run("a/over", pln=9.0)], CATALOGUE) is None
