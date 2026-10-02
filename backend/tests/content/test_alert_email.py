import tomllib
from collections.abc import Mapping
from dataclasses import replace

from app.alerts.render import TEMPLATE_PATH, load_template, render_alert
from tests.alerts.helpers import (
    AS_OF,
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
    assert set(template["event"]) == EVENT_TYPES
    assert set(template["certainty"]) == CERTAINTIES
    assert set(template["grade"]) == GRADES
    assert set(template["title"]) == KINDS
    assert set(template["intro"]) == KINDS
    assert all(value.strip() for section in template.values() for value in section.values())


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
    plain = report(listed(ISAK, percent=None, trending=3, claims=(200,)))
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
    outputs = [
        render_alert("digest", deadline(), AS_OF, [full_report(), plain], 7, recorder),
        render_alert("digest", deadline(rehearsal=True), AS_OF, [], 0, recorder),
        render_alert("digest", deadline(), AS_OF, [], 5, recorder),
        render_alert("news", deadline(), AS_OF, [full_report()], 0, recorder),
        render_alert("breaking", deadline(), AS_OF, [full_report(), plain], 0, recorder),
        render_alert("news", deadline(), AS_OF, [*vocabulary, anchorless], 0, recorder),
    ]
    for message in outputs:
        assert "{" not in message.title + message.text
        assert "}" not in message.title + message.text
    assert seen == set(leaf_paths(TEMPLATE))
