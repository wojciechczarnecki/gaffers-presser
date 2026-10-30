import json
import re
from datetime import UTC, datetime, timedelta

import pytest

from app.content import load_prompt
from app.extraction.evaluation.cases import EvalCase, ExpectedEvent, load_cases
from app.extraction.evaluation.dataset import load_manifest
from app.extraction.evaluation.judging import (
    REPLY_FORMAT,
    CorpusRow,
    JudgingError,
    estimate_export,
    export_inputs,
    import_verdicts,
    parse_quotas,
    sources_from_cases,
    sources_from_corpus,
)
from app.extraction.fixtures import FixtureIndex, FixtureRecord
from app.extraction.linking import PlayerIndex, PlayerRecord, TeamRecord

SEASON = "2026/27"
AT = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
TEAMS = [
    TeamRecord(season=season_label, fpl_id=i, name=name, short_name=short)
    for season_label, i, name, short in [
        (SEASON, 1, "Man City", "MCI"),
        (SEASON, 2, "Arsenal", "ARS"),
    ]
]


def _player(fpl_id, web, first, second, team):
    return PlayerRecord(
        season=SEASON,
        fpl_id=fpl_id,
        web_name=web,
        first_name=first,
        second_name=second,
        team_fpl_id=team,
    )


PLAYERS = [
    _player(5, "Haaland", "Erling", "Haaland", 1),
    _player(7, "Saka", "Bukayo", "Saka", 2),
    _player(8, "Smith", "John", "Smith", 1),
    _player(9, "Smith", "Jake", "Smith", 2),
]
INDEX = PlayerIndex(PLAYERS, TEAMS)
FIXTURES = FixtureIndex(
    [
        FixtureRecord(
            season=SEASON,
            fpl_id=1,
            gameweek=7,
            kickoff_at=AT + timedelta(days=2),
            team_h_fpl_id=1,
            team_a_fpl_id=2,
        ),
        FixtureRecord(
            season=SEASON,
            fpl_id=2,
            gameweek=6,
            kickoff_at=AT - timedelta(days=5),
            team_h_fpl_id=2,
            team_a_fpl_id=1,
        ),
    ]
)


def _case(case_id, *events, tags=(), split="dev", reviewed=True) -> EvalCase:
    return EvalCase(
        id=case_id,
        author_handle="reporter",
        text=f"Post {chr(97 + int(case_id) % 10)}",
        created_at=AT,
        is_repost=False,
        is_reply=False,
        split=split,
        synthetic=False,
        reviewed=reviewed,
        tags=list(tags),
        expected_events=[
            ExpectedEvent(mention=m, fpl_id=i, event_type=t, certainty=c) for m, i, t, c in events
        ],
    )


def _verdict(*events, flag=False, **flags) -> dict:
    return {
        "events": [
            {"player": p, "team": None, "event_type": t, "certainty": c} for p, t, c in events
        ],
        "other_match_lineup": flag,
        **flags,
    }


def _row(x_id, text, created_at=AT, is_source=True) -> CorpusRow:
    return CorpusRow(
        x_id=x_id,
        author_handle="reporter",
        text=text,
        created_at=created_at,
        is_repost=False,
        is_reply=False,
        is_source=is_source,
    )


def _write_verdicts(export, verdicts: dict) -> None:
    mapping = json.loads((export / "mapping.json").read_text())
    (export / "verdicts").mkdir(exist_ok=True)
    for input_id, item in mapping["inputs"].items():
        key = item["case_id"] if item["kind"] == "case" else str(item["post"]["x_id"])
        if key in verdicts:
            (export / "verdicts" / f"{input_id}.json").write_text(json.dumps(verdicts[key]))


def _export_cases(tmp_path, cases, name="export"):
    from app.extraction.evaluation.cases import write_cases

    source = tmp_path / "v1-cases.jsonl"
    write_cases(source, cases)
    out = tmp_path / name
    export_inputs(sources_from_cases(cases), FIXTURES, INDEX, out, seed=1, cases_source=source)
    return out


def test_export_one_input_per_post_with_neutral_ids(tmp_path):
    cases = [_case("101"), _case("102"), _case("103")]
    out = _export_cases(tmp_path, cases)

    names = sorted(p.name for p in (out / "inputs").iterdir())
    assert names[0] == "I0001.md" and "judge-prompt.md" in names
    assert [n for n in names if n != "judge-prompt.md"] == ["I0001.md", "I0002.md", "I0003.md"]
    assert all(re.fullmatch(r"I\d{4}\.md|judge-prompt\.md", n) for n in names)
    one = (out / "inputs" / "I0001.md").read_text()
    assert "Posted at: 2026-10-10T12:00:00+00:00" in one
    assert one.count("previous") == 2  # one fixtures line per club
    assert "Man City: previous v Arsenal (A), 2026-10-05T12:00Z; next v Arsenal (H)" in one
    assert not re.search(r"\b10[123]\b", "".join(n for n in names))
    prompt = (out / "inputs" / "judge-prompt.md").read_text()
    assert prompt.startswith(load_prompt("extraction").text.rstrip())
    assert load_prompt("extraction_prelabel").text in prompt


def test_mapping_outside_inputs(tmp_path):
    out = _export_cases(tmp_path, [_case("101"), _case("102")])

    mapping = json.loads((out / "mapping.json").read_text())
    assert not (out / "inputs" / "mapping.json").exists()
    assert {item["case_id"] for item in mapping["inputs"].values()} == {"101", "102"}
    assert mapping["prompts"] == {
        "extraction": load_prompt("extraction").version,
        "extraction_prelabel": load_prompt("extraction_prelabel").version,
    }
    inputs_text = "".join(p.read_text() for p in (out / "inputs").iterdir())
    assert "101" not in inputs_text and "102" not in inputs_text


def test_export_refuses_a_non_empty_directory(tmp_path):
    out = _export_cases(tmp_path, [_case("101")])
    with pytest.raises(JudgingError):
        export_inputs(sources_from_cases([_case("101")]), FIXTURES, INDEX, out, seed=1)


def test_export_keywords_rest_sample_and_required_ids():
    rows = [
        _row(1, "Man City XI: Haaland starts"),
        _row(2, "Saka on the bench"),
        _row(3, "Lovely weather today"),
        _row(4, "Another quiet post"),
        _row(5, "Third quiet post"),
        _row(6, "Early line-up leak", created_at=AT - timedelta(days=30)),
        _row(7, "Repost of nothing", is_source=False),
        _row(8, "Required quiet post", created_at=AT - timedelta(days=40)),
        _row(9, "Excluded XI"),
    ]

    def pick(**overrides):
        args = dict(
            since=AT - timedelta(days=1),
            until=AT + timedelta(days=1),
            keywords=True,
            rest_sample=1,
            seed=18,
            x_ids=[8],
            exclude={"9"},
        )
        args.update(overrides)
        return sources_from_corpus(rows, **args)

    chosen = pick()
    ids = [s.post.x_id for s in chosen]
    assert {1, 2, 8} <= set(ids) and 6 not in ids and 7 not in ids and 9 not in ids
    assert len(ids) == 4  # keyword posts, the required one and a sample of one of the rest
    assert {s.post.x_id for s in chosen if s.required} == {8}
    assert [s.post.x_id for s in pick()] == ids
    assert [s.post.x_id for s in pick(rest_sample=0)] == [1, 2, 8]


def test_estimate_counts_calls_and_tokens(tmp_path):
    out = _export_cases(tmp_path, [_case("101"), _case("102")])

    estimate = estimate_export(out)

    prompt_chars = len((out / "inputs" / "judge-prompt.md").read_text())
    input_chars = sum(len(p.read_text()) for p in (out / "inputs").glob("I*.md"))
    assert estimate.calls == 2
    assert estimate.tokens > 2 * 3000
    assert estimate.tokens >= (2 * prompt_chars + input_chars) // 4 + 2 * 3000
    with pytest.raises(JudgingError):
        estimate_export(tmp_path / "nowhere")


def _import(out, set_dir, **kwargs):
    return import_verdicts(out, set_dir, INDEX, "claude-test", **kwargs)


def test_import_refuses_an_invalid_verdict(tmp_path):
    out = _export_cases(tmp_path, [_case("101"), _case("102")])
    good = _verdict()
    set_dir = tmp_path / "set"
    set_dir.mkdir()
    bad_variants = [
        {**good, "extra": 1},
        _verdict(("Haaland", "injured", "confirmed")),
        {"events": []},
        {
            "events": [{"player": "Haaland", "event_type": "out", "certainty": "likely", "x": 1}],
            "other_match_lineup": False,
        },
        {**good, "other_match_lineup": "yes"},
        {
            "events": [{"player": "", "event_type": "out", "certainty": "likely"}],
            "other_match_lineup": False,
        },
    ]
    for bad in bad_variants:
        _write_verdicts(out, {"101": good, "102": bad})
        with pytest.raises(JudgingError, match="invalid verdicts"):
            _import(out, set_dir)
        assert not (set_dir / "cases.jsonl").exists()
        assert not (set_dir / "set.json").exists()


def test_import_refuses_a_missing_case_verdict(tmp_path):
    out = _export_cases(tmp_path, [_case("101"), _case("102")])
    _write_verdicts(out, {"101": _verdict()})
    set_dir = tmp_path / "set"
    set_dir.mkdir()

    with pytest.raises(JudgingError, match="102"):
        _import(out, set_dir)
    assert not (set_dir / "cases.jsonl").exists()


def test_unchanged_v1_case_copied_reviewed(tmp_path):
    v1 = _case("101", ("Haaland", 5, "out", "confirmed"), tags=["multi_player"])
    ambiguous = _case("102", ("Smith", 8, "doubt", "likely"))
    out = _export_cases(tmp_path, [v1, ambiguous])
    _write_verdicts(
        out,
        {
            "101": _verdict(("haaland", "out", "confirmed")),  # spelled differently
            "102": _verdict(("Smith", "doubt", "likely")),  # ambiguous: left unlinked
        },
    )
    set_dir = tmp_path / "set"
    set_dir.mkdir()

    report = _import(out, set_dir)

    assert (report.copied, report.changed) == (2, 0)
    assert sorted(load_cases(set_dir / "cases.jsonl"), key=lambda c: c.id) == [v1, ambiguous]


def test_changed_v1_case_unreviewed_with_the_tag(tmp_path):
    v1 = _case("101", ("Haaland", 5, "benched", "confirmed"), tags=["cup_european_lineup"])
    same = _case("102")
    out = _export_cases(tmp_path, [v1, same])
    _write_verdicts(
        out,
        {"101": _verdict(flag=True), "102": _verdict(("Saka", "out", "likely"))},
    )
    set_dir = tmp_path / "set"
    set_dir.mkdir()

    report = _import(out, set_dir)

    assert (report.copied, report.changed) == (0, 2)
    by_id = {c.id: c for c in load_cases(set_dir / "cases.jsonl")}
    assert by_id["101"].reviewed is False
    assert by_id["101"].expected_events == []
    assert by_id["101"].tags == ["cup_european_lineup", "other_match_lineup"]
    assert by_id["102"].reviewed is False
    assert [(e.mention, e.fpl_id) for e in by_id["102"].expected_events] == [("Saka", 7)]


def test_a_flag_only_change_makes_the_v1_case_unreviewed(tmp_path):
    gains = _case("101", ("Haaland", 5, "out", "confirmed"))
    loses = _case("102", ("Haaland", 5, "out", "confirmed"), tags=["other_match_lineup"])
    out = _export_cases(tmp_path, [gains, loses])
    _write_verdicts(
        out,
        {
            "101": _verdict(("Haaland", "out", "confirmed"), flag=True),
            "102": _verdict(("Haaland", "out", "confirmed"), flag=False),
        },
    )
    set_dir = tmp_path / "set"
    set_dir.mkdir()

    report = _import(out, set_dir)

    assert (report.copied, report.changed) == (0, 2)
    by_id = {c.id: c for c in load_cases(set_dir / "cases.jsonl")}
    assert by_id["101"].reviewed is False
    assert by_id["101"].tags == ["other_match_lineup"]
    assert by_id["102"].reviewed is False
    assert by_id["102"].tags == []
    assert [(e.fpl_id, e.event_type) for e in by_id["101"].expected_events] == [(5, "out")]


def _post_export(tmp_path, rows, **kwargs):
    out = tmp_path / "corpus-export"
    sources = sources_from_corpus(
        rows,
        since=None,
        until=None,
        keywords=False,
        rest_sample=0,
        seed=1,
        x_ids=kwargs.get("x_ids", []),
        exclude=set(),
    )
    export_inputs(sources, FIXTURES, INDEX, out, seed=1)
    return out


def test_select_tiers_and_quotas(tmp_path):
    rows = [_row(1000 + i, f"post {i}") for i in range(40)]
    out = _post_export(tmp_path, rows, x_ids=[1039])
    verdicts = {}
    for i in range(40):
        if i < 12:
            verdicts[str(1000 + i)] = _verdict(flag=True)
        elif i < 24:
            verdicts[str(1000 + i)] = _verdict(("Haaland", "benched", "likely"))
        elif i < 32:
            verdicts[str(1000 + i)] = _verdict(("Haaland", "out", "likely"))
        else:
            verdicts[str(1000 + i)] = _verdict()
    _write_verdicts(out, verdicts)
    set_dir = tmp_path / "set"
    set_dir.mkdir()

    report = _import(
        out, set_dir, select=14, seed=3, quotas=parse_quotas("other=4,lineup=5,event=3")
    )

    assert report.candidates == 40
    cases = load_cases(set_dir / "cases.jsonl")
    ids = {int(c.id) for c in cases}
    assert len(cases) == 14
    assert 1039 in ids  # required comes first
    tagged = [c for c in cases if "other_match_lineup" in c.tags]
    lineups = [c for c in cases if any(e.event_type == "benched" for e in c.expected_events)]
    others = [c for c in cases if c.expected_events and "benched" not in str(c.expected_events)]
    assert (len(tagged), len(lineups), len(others)) == (4, 5, 3)
    again = tmp_path / "set2"
    again.mkdir()
    _import(out, again, select=14, seed=3, quotas=parse_quotas("other=4,lineup=5,event=3"))
    assert [c.id for c in load_cases(again / "cases.jsonl")] == [c.id for c in cases]


def test_short_tier_leaves_its_places_to_the_next(tmp_path):
    rows = [_row(2000 + i, f"post {i}") for i in range(10)]
    out = _post_export(tmp_path, rows)
    _write_verdicts(
        out, {str(2000 + i): _verdict(("Haaland", "benched", "likely")) for i in range(10)}
    )
    set_dir = tmp_path / "set"
    set_dir.mkdir()

    _import(out, set_dir, select=8, quotas=parse_quotas("other=5,lineup=3"))

    assert len(load_cases(set_dir / "cases.jsonl")) == 8


def test_imported_cases_unreviewed_with_the_judge_in_the_manifest(tmp_path):
    rows = [_row(3009, "XI leak"), _row(3002, "quiet")]
    out = _post_export(tmp_path, rows)
    _write_verdicts(
        out, {"3009": _verdict(("Haaland", "benched", "likely")), "3002": _verdict(flag=True)}
    )
    set_dir = tmp_path / "set"
    set_dir.mkdir()

    _import(out, set_dir, now=AT)
    # a second import adds a second entry and skips the ids already in the set
    report = _import(out, set_dir, now=AT)

    cases = {c.id: c for c in load_cases(set_dir / "cases.jsonl")}
    assert all(c.reviewed is False and c.synthetic is False for c in cases.values())
    assert cases["3009"].split == "test" and cases["3002"].split == "dev"  # x_id % 10 < 3
    assert cases["3002"].tags == ["other_match_lineup"]
    assert sorted(report.skipped_in_set) == ["3002", "3009"]
    manifest = load_manifest(set_dir)
    assert [e.judge for e in manifest.prelabels] == ["claude-test", "claude-test"]
    assert manifest.prelabels[0].prompt_versions.startswith("extraction@")
    assert manifest.prelabels[0].inputs == 2


def test_import_links_unique_names_and_leaves_ambiguous_null(tmp_path):
    out = _post_export(tmp_path, [_row(4001, "x")])
    _write_verdicts(
        out,
        {
            "4001": _verdict(
                ("Haaland", "out", "likely"),
                ("Smith", "doubt", "likely"),
                ("Nobody", "out", "likely"),
            )
        },
    )
    set_dir = tmp_path / "set"
    set_dir.mkdir()

    _import(out, set_dir)

    [case] = load_cases(set_dir / "cases.jsonl")
    assert [(e.mention, e.fpl_id) for e in case.expected_events] == [
        ("Haaland", 5),
        ("Smith", None),
        ("Nobody", None),
    ]


def test_parse_quotas():
    assert parse_quotas("other=1")["lineup"] == 35
    with pytest.raises(JudgingError):
        parse_quotas("bogus=1")


def test_unwatched_authors_are_left_out_at_export_and_at_import(tmp_path):
    rows = [
        _row(5001, "XI of a watched account"),
        _row(5002, "XI of a stranger").model_copy(update={"author_handle": "stranger"}),
    ]
    sources = sources_from_corpus(
        rows,
        since=None,
        until=None,
        keywords=False,
        rest_sample=0,
        seed=1,
        x_ids=[],
        exclude=set(),
        authors=["Reporter"],
    )
    assert [s.post.x_id for s in sources] == [5001]

    out = _post_export(tmp_path, rows)
    _write_verdicts(out, {"5001": _verdict(), "5002": _verdict()})
    set_dir = tmp_path / "set"
    set_dir.mkdir()
    (set_dir / "set.json").write_text(json.dumps({"version": "v2", "list_handles": ["reporter"]}))

    report = _import(out, set_dir)

    assert [c.id for c in load_cases(set_dir / "cases.jsonl")] == ["5001"]
    assert report.skipped_unwatched == ["5002"]


def test_reply_format_lists_five_types():
    for event_type in ("out", "doubt", "benched", "confirmed_starter", "available"):
        assert f'"{event_type}"' in REPLY_FORMAT
    assert '"other_match_absence"' in REPLY_FORMAT and '"cup_injury"' in REPLY_FORMAT


def test_flags_become_tags(tmp_path):
    v1 = _case("101", ("Haaland", 5, "out", "confirmed"))
    out = _export_cases(tmp_path, [v1])
    _write_verdicts(out, {"101": _verdict(other_match_absence=True, cup_injury=True)})
    set_dir = tmp_path / "set"
    set_dir.mkdir()

    report = _import(out, set_dir)

    assert (report.copied, report.changed) == (0, 1)
    [case] = load_cases(set_dir / "cases.jsonl")
    assert case.tags == ["other_match_absence", "cup_injury"]
    assert case.reviewed is False


def test_unchanged_case_needs_all_three_flag_tags_equal(tmp_path):
    same = _case(
        "101", ("Haaland", 5, "out", "confirmed"), tags=["other_match_absence", "cup_injury"]
    )
    differs = _case("102", ("Haaland", 5, "out", "confirmed"), tags=["cup_injury"])
    out = _export_cases(tmp_path, [same, differs])
    verdict = ("Haaland", "out", "confirmed")
    _write_verdicts(
        out,
        {
            "101": _verdict(verdict, other_match_absence=True, cup_injury=True),
            "102": _verdict(verdict),
        },
    )
    set_dir = tmp_path / "set"
    set_dir.mkdir()

    report = _import(out, set_dir)

    assert (report.copied, report.changed) == (1, 1)
    by_id = {c.id: c for c in load_cases(set_dir / "cases.jsonl")}
    assert by_id["101"].reviewed is True
    assert by_id["102"].tags == [] and by_id["102"].reviewed is False


def test_old_verdict_without_new_flags_imports(tmp_path):
    out = _post_export(tmp_path, [_row(5001, "x")])
    _write_verdicts(out, {"5001": {"events": [], "other_match_lineup": False}})
    set_dir = tmp_path / "set"
    set_dir.mkdir()

    _import(out, set_dir)

    [case] = load_cases(set_dir / "cases.jsonl")
    assert case.tags == []


def test_availability_keywords_keep_fitness_posts():
    rows = [
        _row(6001, "Palmer is NOT suspended"),
        _row(6002, "Evanilson rested for GW9"),
        _row(6003, "weather is nice"),
        _row(6004, "Haaland starts today"),
    ]
    common = dict(since=None, until=None, rest_sample=0, seed=1, x_ids=[], exclude=set())
    lineup_only = sources_from_corpus(rows, keywords=True, **common)
    both = sources_from_corpus(rows, keywords=True, availability_keywords=True, **common)
    only_availability = sources_from_corpus(
        rows, keywords=False, availability_keywords=True, **common
    )
    assert {s.post.x_id for s in lineup_only} == {6004}
    assert {s.post.x_id for s in both} == {6001, 6002, 6004}
    assert {s.post.x_id for s in only_availability} == {6001, 6002}


def test_absence_and_available_tiers(tmp_path):
    rows = [_row(7000 + i, f"post {i}") for i in range(30)]
    out = _post_export(tmp_path, rows)
    verdicts = {}
    for i in range(30):
        x_id = str(7000 + i)
        if i < 8:
            verdicts[x_id] = _verdict(other_match_absence=True)
        elif i < 14:
            verdicts[x_id] = _verdict(("Haaland", "available", "likely"))
        elif i < 24:
            verdicts[x_id] = _verdict(("Haaland", "out", "likely"))
        else:
            verdicts[x_id] = _verdict()
    _write_verdicts(out, verdicts)
    set_dir = tmp_path / "set"
    set_dir.mkdir()

    _import(out, set_dir, select=10, seed=2, quotas=parse_quotas("absence=3,available=2,event=5"))

    cases = load_cases(set_dir / "cases.jsonl")
    absence = [c for c in cases if "other_match_absence" in c.tags]
    available = [c for c in cases if any(e.event_type == "available" for e in c.expected_events)]
    assert (len(cases), len(absence), len(available)) == (10, 3, 2)
    with pytest.raises(JudgingError):
        parse_quotas("bogus=1")
    assert parse_quotas("other=1")["absence"] == 15
