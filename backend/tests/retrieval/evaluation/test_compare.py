import json

from typer.testing import CliRunner

from app.core.eval_results import write_json_atomic
from app.retrieval.evaluation.cli import app


def result(model, set_version="v2", split="dev", recall=0.5, header=True, judge="judge-a", k=60):
    free = {"recall_at_5": 0.9, "recall_at_10": 0.95, "mrr": 0.8, "n": 3, "unjudged_share": 0.1}
    data = {
        "player": {
            "counts": {},
            "rows": [
                {
                    "mode": mode,
                    "variant": "web_name",
                    "depth": depth,
                    "queries": 4,
                    "recall_at_cut": recall,
                    "recall_at_10": 0.6,
                    "empty_cuts_with_miss": 1,
                    "unjudged_share": 0.25,
                }
                for mode in ("fulltext", "hybrid")
                for depth in (20, 50)
            ],
        },
        "free": {"modes": {"hybrid": {"all": free, "en": free}}, "queries_without_relevant": 0},
    }
    if header:
        data["header"] = {
            "set_version": set_version,
            "split": split,
            "model": model,
            "reference_judge": judge,
            "k": k,
            "depths": [20, 50],
        }
    return data


def write(tmp_path, name, **kwargs):
    path = tmp_path / name
    write_json_atomic(path, result(**kwargs))
    return str(path)


def run(*paths):
    return CliRunner().invoke(app, ["compare", *paths])


def test_two_models_print_one_table_with_a_row_per_configuration(tmp_path):
    a = write(tmp_path, "a.json", model="openai/small")
    b = write(tmp_path, "b.json", model="other/embed", recall=0.7)
    out = run(a, b)
    assert out.exit_code == 0, out.output
    lines = out.stdout.splitlines()
    player_rows = [line for line in lines if "web_name" in line]
    assert len(player_rows) == 2 * 4
    assert sum("openai/small" in line for line in player_rows) == 4
    assert any("other/embed" in line and "0.700" in line and "0.250" in line for line in lines)
    free_rows = [line for line in lines if line.split()[2:3] in (["all"], ["en"])]
    assert len(free_rows) == 2 * 2
    assert "set v2  split dev  files 2" in out.stdout


def test_a_file_of_another_set_version_is_refused_by_name(tmp_path):
    a = write(tmp_path, "a.json", model="m1")
    b = write(tmp_path, "b.json", model="m2", set_version="v3")
    out = run(a, b)
    assert out.exit_code == 1
    assert "b.json" in out.stderr and "set_version" in out.stderr
    assert out.stdout == ""


def test_a_file_of_another_split_is_refused_by_name(tmp_path):
    a = write(tmp_path, "a.json", model="m1")
    b = write(tmp_path, "b.json", model="m2", split="test")
    out = run(a, b)
    assert out.exit_code == 1
    assert "b.json" in out.stderr and "split" in out.stderr
    assert out.stdout == ""


def test_a_file_without_a_header_is_refused_and_nothing_is_printed(tmp_path):
    a = write(tmp_path, "a.json", model="m1")
    b = write(tmp_path, "old.json", model="m2", header=False)
    out = run(a, b)
    assert out.exit_code == 1
    assert "no header" in out.stderr and "old.json" in out.stderr
    assert out.stdout == ""


def test_an_unreadable_file_is_named(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    out = run(str(bad))
    assert out.exit_code == 1
    assert "bad.json" in out.stderr


def test_free_only_results_compare_too(tmp_path):
    data = result("m1")
    data["player"] = {"counts": {}, "rows": []}
    path = tmp_path / "free.json"
    path.write_text(json.dumps(data))
    out = run(str(path))
    assert out.exit_code == 0
    assert "recall@cut" not in out.stdout


def test_a_file_with_another_reference_judge_or_k_is_refused_by_name(tmp_path):
    a = write(tmp_path, "a.json", model="m1")
    for name, kwargs, key in (
        ("judge.json", {"judge": "judge-b"}, "reference_judge"),
        ("k.json", {"k": 10}, "k"),
    ):
        other = write(tmp_path, name, model="m2", **kwargs)
        out = run(a, other)
        assert out.exit_code == 1
        assert name in out.stderr and key in out.stderr
        assert out.stdout == ""


def test_the_depths_of_every_file_are_printed(tmp_path):
    a = write(tmp_path, "a.json", model="m1")
    out = run(a)
    assert out.exit_code == 0, out.output
    assert "a.json  model m1  depths 20,50" in out.stdout


def test_a_headed_result_of_another_evaluation_is_refused_without_a_traceback(tmp_path):
    a = write(tmp_path, "a.json", model="m1")
    presser = tmp_path / "presser.json"
    write_json_atomic(presser, {"header": {"set_version": "v1", "model": "m"}, "cases": []})
    no_free = tmp_path / "no_free.json"
    write_json_atomic(no_free, {"header": {"set_version": "v2", "split": "dev", "model": "m"}})
    for path in (presser, no_free):
        out = run(a, str(path))
        assert out.exit_code == 1
        assert path.name in out.stderr and "not a result of the retrieval evaluation" in out.stderr
        assert out.exception is None or isinstance(out.exception, SystemExit)
