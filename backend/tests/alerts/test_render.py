import ast
from datetime import UTC, datetime
from pathlib import Path

from app.alerts.render import load_template, render_alert
from app.alerts.schemas import ManagerRef
from app.core.local_time import format_local
from tests.alerts.helpers import (
    AS_OF,
    SAKA,
    corroboration,
    deadline,
    full_report,
    listed,
    player_ref,
    report,
)

TEMPLATE = load_template()
ISAK = player_ref(2, "Isak", "Newcastle")


def test_player_section_fields():
    message = render_alert("digest", deadline(), AS_OF, [full_report()], 0)
    text = message.text

    assert "Saka" in text and "Arsenal" in text
    for name in ("Jan Kowalski", "Kowalski FC", "Ewa Nowak", "Nowak XI"):
        assert name in text
    assert "23.4" in text
    assert TEMPLATE["category"]["trending"].format(count=4) in text
    assert TEMPLATE["event"]["out"] in text
    assert TEMPLATE["certainty"]["likely"] in text
    assert TEMPLATE["grade"]["medium"] in text
    assert TEMPLATE["section"]["accounts"].format(supporting=2, contradicting=1) in text
    assert TEMPLATE["note"]["reversal"] in text
    assert TEMPLATE["note"]["search_failed"] in text
    for url in (
        "https://x.com/anchoracct/status/100",
        "https://x.com/second/status/101",
        "https://x.com/third/status/102",
        "https://x.com/doubter/status/103",
    ):
        assert url in text
    assert "@origin" in text
    marked = [line for line in text.splitlines() if TEMPLATE["link"]["new_marker"] in line]
    assert {("status/100" in line) or ("status/102" in line) for line in marked} == {True}
    assert len(marked) == 2


def test_notes_only_when_flagged():
    plain = report(
        listed(SAKA, managers=(ManagerRef("Jan Kowalski", "Kowalski FC"),)),
        corroboration(SAKA),
    )
    text = render_alert("news", deadline(), AS_OF, [plain], 0).text
    assert TEMPLATE["note"]["reversal"] not in text
    assert TEMPLATE["note"]["search_failed"] not in text
    assert TEMPLATE["link"]["new_marker"] not in text
    assert "23.4" not in text
    failed = report(listed(SAKA), corroboration(SAKA), search_failed=True)
    assert (
        TEMPLATE["note"]["search_failed"]
        in render_alert("news", deadline(), AS_OF, [failed], 0).text
    )


def test_players_keep_the_given_order():
    first = report(listed(ISAK, claims=(200,)), corroboration(ISAK, 200))
    second = full_report()
    text = render_alert("digest", deadline(), AS_OF, [first, second], 0).text
    assert text.index("Isak") < text.index("Saka")


def test_digest_empty_and_no_news_line():
    empty = render_alert("digest", deadline(), AS_OF, [], 0).text
    assert TEMPLATE["digest"]["empty"] in empty
    with_count = render_alert("digest", deadline(), AS_OF, [full_report()], 12).text
    assert TEMPLATE["digest"]["no_news"].format(count=12) in with_count
    assert TEMPLATE["digest"]["empty"] not in with_count
    news = render_alert("news", deadline(), AS_OF, [full_report()], 12).text
    assert TEMPLATE["digest"]["no_news"].format(count=12) not in news


def test_times_are_warsaw():
    as_of = datetime(2026, 10, 4, 14, 0, tzinfo=UTC)
    message = render_alert("digest", deadline(), as_of, [full_report()], 0)
    assert format_local(as_of) == "2026-10-04 16:00"
    assert "2026-10-04 16:00" in message.text
    assert "2026-10-04 18:00" in message.text  # the deadline at 16:00 UTC
    assert "2026-10-04 14:30" in message.text  # a post at 12:30 UTC
    assert "14:00" not in message.text.replace("14:30", "")


def test_rehearsal_title_names_the_rehearsal_moment():
    title = render_alert("digest", deadline(rehearsal=True), AS_OF, [], 0).title
    assert "2026-10-04 18:00" in title
    assert "GW" not in title


def test_alerts_code_has_no_polish_literals():
    polish = set("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")
    root = Path(__file__).resolve().parents[2] / "app" / "alerts"
    files = list(root.rglob("*.py"))
    assert files
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not polish & set(node.value), f"{path.name}: {node.value!r}"
