import itertools
import re
import tomllib

from app.content import PROMPTS_DIR, load_prompt

CONTENT = PROMPTS_DIR.parent


def test_writer_prompt_loads_with_a_version():
    prompt = load_prompt("presser_writer")
    assert prompt.version >= 1
    assert "autosub" in prompt.text
    assert "1500" in prompt.text


def test_writer_prompt_v4_rules():
    prompt = load_prompt("presser_writer")
    assert prompt.version == 4
    for needle in (
        "only where it sounds natural",
        "never copy",
        "gameweek_rank",
        "not a source of phrases",
        "🌍",
        "1500",
        "autosub",
        "średnia ligowa",
        "from 10 000 is never quoted exactly",
        "fewer than 1 000 places is quoted exactly",
        'never "tys."',
        'never write "1000k"',
        "never cross a threshold",
    ):
        assert needle in prompt.text, needle
    assert prompt.text.index("`table`") < prompt.text.index("`overall`")


def test_judge_prompt_checks_ranks():
    prompt = load_prompt("presser_judge")
    assert prompt.version == 3
    for needle in ("rank", "threshold", "rounded", "1,2 mln", "o 15k miejsc", "tuż za top 10k"):
        assert needle in prompt.text, needle


def test_glossary_has_69_complete_terms():
    terms = tomllib.loads((CONTENT / "presser_glossary.toml").read_text(encoding="utf-8"))["term"]
    assert len(terms) == 69
    for term in terms:
        for field in ("term", "meaning", "example", "source"):
            assert term[field].strip(), field


FLAGGED_PHRASES = (
    "odskok od peletonu",
    "peleton",
    "zielona strzałka",
    "transfer tygodnia w złą stronę",
    "punktów netto, ",
)
HEADER_MARKS = ("🏆", "🤦", "\u00a9\ufe0f", "🪑", "📊", "🌍")
HEADER_LABEL = re.compile(r"^\S+ \*[^*]+:\*\s*")


def _examples() -> list[list[str]]:
    text = (CONTENT / "presser_style_examples.md").read_text(encoding="utf-8")
    parts = re.split(r"^## Example.*$", text, flags=re.MULTILINE)[1:]
    return [[p.strip() for p in part.strip().split("\n\n")] for part in parts]


def _sentences(paragraphs: list[str]) -> list[str]:
    found = []
    for paragraph in paragraphs:
        body = HEADER_LABEL.sub("", paragraph)
        found += [s.strip().casefold() for s in re.split(r"(?<=[.!?])\s+", body) if s.strip()]
    return found


def test_style_examples_six_headers_no_shared_sentence():
    examples = _examples()
    assert len(examples) == 3
    sentence_sets = []
    word_runs = []
    for paragraphs in examples:
        title, *sections = paragraphs
        assert title.startswith("🎙️")
        assert len(sections) == 6
        starts = [p[0 : len(mark)] for p, mark in zip(sections, HEADER_MARKS, strict=True)]
        assert starts == list(HEADER_MARKS)
        assert sum(len(p) for p in paragraphs) + 2 * len(paragraphs) < 1500
        assert "gw rank" in "\n".join(sections).casefold()
        sentence_sets.append(set(_sentences(sections)))
        words = re.findall(r"\w+", HEADER_LABEL.sub("", "\n".join(sections)).casefold())
        word_runs.append({tuple(words[n : n + 5]) for n in range(len(words) - 4)})
    for first, second in itertools.combinations(range(3), 2):
        assert not sentence_sets[first] & sentence_sets[second]
        assert not word_runs[first] & word_runs[second]
    whole = "\n".join("\n".join(p) for p in examples).casefold()
    for phrase in FLAGGED_PHRASES:
        assert phrase not in whole, phrase


def test_style_examples_round_ranks_like_the_prompt():
    whole = "\n".join("\n".join(p) for p in _examples())
    assert "tys." not in whole
    exact = [int(n.replace(" ", "")) for n in re.findall(r"\b\d{1,3}(?: \d{3})+\b|\d{4,}", whole)]
    assert exact
    assert all(n < 10_000 for n in exact), exact
    assert not re.search(r",0 ?(k|mln)\b|\b1000k\b", whole)
    assert not re.search(r"średni\w*(?! lig)", whole)
