import tomllib
from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta

from app.alerts.render import TEMPLATE_PATH, load_template, render_alert
from tests.alerts.helpers import (
    AS_OF,
    citation,
    corroboration,
    deadline,
    full_report,
    listed,
    player_ref,
    report,
)

TEMPLATE = load_template()
ISAK = player_ref(2, "Isak", "Newcastle")

CERTAINTIES = {"confirmed", "likely", "rumour"}
EVENT_TYPES = {"out", "doubt", "benched", "confirmed_starter"}
GRADES = {"high", "medium", "low"}
KINDS = {"digest", "news", "breaking"}


def test_template_file_is_valid_toml_with_every_vocabulary():
    template = tomllib.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    for section in ("event", "group", "summary"):
        assert set(template[section]) - {"separator"} == EVENT_TYPES
    assert set(template["palette"]) == EVENT_TYPES | {"base"}
    assert GRADES <= set(template["grade"])
    assert set(template["title"]) == KINDS
    assert set(template["kind"]) == KINDS
    assert len(template["header"]["weekdays"]) == 7
    assert all(str(value).strip() for value in leaf_values(template))


def leaf_values(data):
    for value in data.values():
        if isinstance(value, dict):
            yield from leaf_values(value)
        else:
            yield value


def test_loaded_template_matches_the_file():
    assert load_template()["title"]["digest"].strip()


class Recorder(Mapping):
    def __init__(self, data, seen, path=()):
        self._data, self._seen, self._path = data, seen, path

    def __getitem__(self, key):
        value = self._data[key]
        path = (*self._path, key)
        if isinstance(value, dict):
            return Recorder(value, self._seen, path)
        self._seen.add(path)
        return value

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)


def leaf_paths(data, prefix=()):
    for key, value in data.items():
        if isinstance(value, dict):
            yield from leaf_paths(value, (*prefix, key))
        else:
            yield (*prefix, key)


def test_template_has_every_key_and_renders():
    seen: set[tuple] = set()
    recorder = Recorder(TEMPLATE, seen)
    vocabulary = [
        report(
            listed(ISAK, claims=(200,)),
            corroboration(ISAK, 200, event_type=event, certainty=certainty, level=level),
        )
        for event in EVENT_TYPES
        for certainty in CERTAINTIES
        for level in GRADES
    ]
    anchorless = report(
        listed(ISAK, claims=(200,)),
        replace(corroboration(ISAK, 200), anchor=None, grade=None),
    )
    many = report(
        listed(ISAK, claims=(200,)),
        corroboration(ISAK, 200, supporting=[citation(201 + i, f"s{i}") for i in range(4)]),
    )
    outputs = [
        render_alert("digest", deadline(), AS_OF, [full_report(), many], recorder),
        render_alert("digest", deadline(rehearsal=True), AS_OF, [], recorder),
        render_alert("news", deadline(), AS_OF, [full_report()], recorder),
        render_alert("breaking", deadline(), AS_OF, [full_report()], recorder),
        render_alert("news", deadline(), AS_OF, [*vocabulary, anchorless], recorder),
        # ages: minutes, hours (marked new), yesterday, days
        render_alert(
            "digest", deadline(), AS_OF - timedelta(hours=1, minutes=10), [many], recorder
        ),
        render_alert("digest", deadline(), AS_OF + timedelta(days=1), [many], recorder),
        render_alert("digest", deadline(), AS_OF + timedelta(days=5), [many], recorder),
    ]
    for message in outputs:
        for part in (message.title, message.text, message.html or ""):
            assert "{" not in part and "}" not in part
    assert seen == set(leaf_paths(TEMPLATE))
