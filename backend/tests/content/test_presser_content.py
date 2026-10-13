import itertools
import re
import tomllib

from app.content import PROMPTS_DIR, load_prompt

CONTENT = PROMPTS_DIR.parent


def test_writer_prompt_loads_with_a_version():
    prompt = load_prompt("presser_writer")
    assert prompt.version >= 1
    assert "autosub" in prompt.text
    assert "2500" in prompt.text


WRITER_HEADERS = (
    "🏆 *Manager kolejki:*",
    "🤦 *Wtopa kolejki:*",
    "©️ *Kapitanowie:*",
    "⚽ *Z boiska:*",
    "🪑 *Ławka i transfery:*",
    "🃏 *Chipy:*",
    "📊 *Tabela:*",
    "🌍 *Overall:*",
)


def test_writer_prompt_v13_rules():
    prompt = load_prompt("presser_writer")
    assert prompt.version == 13
    positions = [prompt.text.index(header) for header in WRITER_HEADERS]
    assert positions == sorted(positions)
    assert prompt.text.index("⚽") < prompt.text.index("🪑")
    assert "1500" not in prompt.text
    for needle in (
        "2500",
        "empty_sections",
        "transfer_wins",
        "paid_off",
        "key_moments",
        "swing",
        "neighbour_gameweeks",
        "podium_changes",
        "level on points",
        "penalty_saved",
        "kept the clean sheet although his club conceded",
        "team_name",
        "flop by the team followed by its owner",
        "never with his name added",
        "`swing` is greater than `margin`",
        "Ending or breaking a streak",
        "never correct yourself",
        "different players in `captaincy.groups`",
        'never "nikt nie…"',
        '"1,01M", "1,03M" or "1M"',
        "only where it sounds natural",
        "never copy",
        "gameweek_rank",
        "not a source of phrases",
        "🌍",
        "autosub",
        "średnia ligowa",
        "from 10 000 is never quoted exactly",
        "fewer than 1 000 places is quoted exactly",
        'never "tys." or "mln"',
        '"1,6M"',
        "or land on it",
        "nieznacznie wypadł poza top 1M",
        'never write "1000k"',
        "never cross a threshold",
        "breakdown",
        "decisive",
        "bonus_missed_by_one",
        "missed_bonus",
        "hat_trick",
        "keeper_wall",
        "big_miss",
        "goal_from_nothing",
        "expected_goals_conceded",
        "punchline",
        "never a bare xG",
        "already in captain\n  points",
        "`nth_of_season`, `gameweek_wins_to_date` and `gameweek_flops_to_date`",
        "`captain_blank_streak`\n  of 2 or more",
        "neither a count nor a streak",
        "counted name by name",
        "`captaincy.groups`",
        "`chips.counts`",
        "never from the rank alone",
        "is in the top 1M, not the top\n  100k",
        "Decline the name in Polish",
        "`clean_sheet_kept`",
        "`against_xgc` true",
        "goals, assists, DefCon, saves, a yellow card, bonus",
        "never the bonus when\n  another stat decided",
        "never the moment's",
        "never say his club lost the clean sheet",
        "empty `transfer_wins` and `transfer_misses` never mean nobody made a transfer",
        "`🎙️ *Presser GW<gameweek> — <league>*`",
        "`league_best` or `league_worst`",
        "`nth_of_season` 1 and `streak` 1 is simply his first flop",
        "Judge a chip by its `effect`",
        "Never give a player, a bench, an autosub or a transfer to another manager",
        "Never present a personal record as the league's",
        "Tell each player's story once",
        "only when he is in `biggest_climbers` or `biggest_fallers`",
        "never with more than one decimal",
        "A captain's points always belong in",
        "the multipliers of the\n  pair are in its `swing`",
    ):
        assert needle in prompt.text, needle
    assert prompt.text.index("`table`") < prompt.text.index("`overall`")
    assert "mln" not in prompt.text.replace('"mln"', "")


def test_judge_prompt_v9_checks_counts_streaks_and_rounding():
    prompt = load_prompt("presser_judge")
    assert prompt.version == 9
    for needle in (
        "pitch",
        "key moment",
        "swing",
        "margin",
        "transfer gain",
        "paid off",
        "rivalry",
        "neighbour",
        "podium",
        "level on points",
        "team name",
        "rank",
        "threshold",
        "rounded",
        "1,2M",
        "o 15k miejsc",
        "tuż za top 10k",
        "breakdown",
        "decisive",
        "missed a bonus by one BPS",
        "hat-trick",
        "keeper wall",
        "big miss",
        "goal from nothing",
        "xG",
        "captain points",
        "drugi raz z\nrzędu",
        "`captain_blank_streak` is 2 or more",
        "onto a threshold or across it",
        "nieznacznie wypadł poza top 1M",
        '",0"',
        "quoted exactly",
        "A number of managers is `supported` only when",
        "given to the wrong manager",
        "A personal record",
        "`biggest_climbers` or `biggest_fallers`",
        "more than one decimal",
        "a movement in places has no threshold",
        '"kolejny raz") is',
        "`captaincy.groups`",
        "`chips.counts`",
        "only when that threshold is in his `entered` or `left`",
        '"1,0M+"',
        "sam gol dał swing 14",
        "`against_xgc` is false or `null`",
        "`unlucky_sub`",
        "only notable transfers",
        "A comparison that is\nfalse",
        "A chip told\nagainst its `effect`",
        "a winner's or a flop's `streak`",
    ):
        assert needle in prompt.text, needle
    assert "mln" not in prompt.text


def test_glossary_prefers_roznica_and_keeps_differential_allowed():
    terms = tomllib.loads((CONTENT / "presser_glossary.toml").read_text(encoding="utf-8"))["term"]
    (entry,) = [term for term in terms if "różnica" in term["term"]]
    assert "hipsterski wybór" in entry["term"]
    assert "najpopularniejsze" in entry["meaning"] and "sporadycznie" in entry["meaning"]
    assert "differential" in entry["meaning"] and "dyferencjał" in entry["meaning"]


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
HEADER_MARKS = ("🏆", "🤦", "\u00a9\ufe0f", "⚽", "🪑", "🃏", "📊", "🌍")
# a winner's or a flop's opening "Team Name Owner": capitalised words, "i" between them
OPENING_NAMES = re.compile(r"[A-ZĄĆĘŁŃÓŚŹŻ]\w*(?: (?:i|[A-ZĄĆĘŁŃÓŚŹŻ]\w*))*")
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


def test_style_examples_eight_headers_no_shared_sentence():
    examples = _examples()
    assert len(examples) == 3
    sentence_sets = []
    word_runs = []
    for paragraphs in examples:
        title, *sections = paragraphs
        assert title.startswith("🎙️")
        assert len(sections) == 8
        starts = [p[0 : len(mark)] for p, mark in zip(sections, HEADER_MARKS, strict=True)]
        assert starts == list(HEADER_MARKS)
        assert sum(len(p) for p in paragraphs) + 2 * len(paragraphs) < 2500
        # the winner and the flop open with "team + owner": the owner is the last word, and
        # its stem matches any case of his name in "Z boiska" ("Zbycha" -> "Zbychu")
        owners = [
            OPENING_NAMES.match(HEADER_LABEL.sub("", sections[n]))[0].split()[-1] for n in (0, 1)
        ]
        assert any(owner[:4] in sections[3] for owner in owners), owners
        assert "gw rank" in "\n".join(sections).casefold()
        pitch = sections[3].casefold()
        assert any(stat in pitch for stat in ("gol", "bonus", "żółt", "defcon", "bps")), pitch
        assert any(punchline in pitch for punchline in ("xg", "pech", "z niczego")), pitch
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
    assert "mln" not in whole
    assert not re.search(r",0 ?(k|M)\b|\b1000k\b", whole)
    assert not re.search(r"średni\w*(?! lig)", whole)
