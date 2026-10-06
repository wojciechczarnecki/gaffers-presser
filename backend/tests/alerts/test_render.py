import ast
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.alerts.render import age_text, load_template, plural_form, render_alert
from app.alerts.schemas import ManagerRef
from app.corroboration.schemas import Claim, Corroboration, RetrievalReport
from tests.alerts.helpers import (
    AS_OF,
    POST_TIME,
    SAKA,
    citation,
    corroboration,
    deadline,
    full_report,
    listed,
    player_ref,
    post_ref,
    report,
)

TEMPLATE = load_template()
MARKER = TEMPLATE["sources"]["new_marker"]
ISAK = player_ref(2, "Isak", "Newcastle")
PALMER = player_ref(3, "Palmer", "Chelsea")
HALL = player_ref(4, "Hall", "Newcastle")


def player_report(player, event_type="doubt", supporters=0, percent="10", level="medium", **kw):
    supporting = [citation(500 + player.fpl_id * 10 + i, f"s{i}") for i in range(supporters)]
    return report(
        listed(player, percent=percent, claims=(player.fpl_id * 100,)),
        corroboration(
            player,
            anchor_x_id=player.fpl_id * 100,
            supporting=supporting,
            event_type=event_type,
            level=level,
            **kw,
        ),
    )


def test_card_shows_the_news_grade_accounts_age_and_sources():
    message = render_alert("digest", deadline(), AS_OF, [full_report()])
    text = message.text

    fresh = TEMPLATE["age"]["fresh"]
    assert f"Saka {fresh} (Arsenal) — " + TEMPLATE["event"]["out"] in text
    assert TEMPLATE["grade"]["medium"] in text
    details = TEMPLATE["grade"]["separator"].join(
        (
            TEMPLATE["grade"]["medium"],
            TEMPLATE["accounts"]["few"].format(count=3),  # the anchor and 2 supporting
            TEMPLATE["accounts"]["against"].format(count=1),
            TEMPLATE["age"]["hours"].format(count=1),  # the anchor is 90 minutes old
        )
    )
    assert details in text and details in message.html
    assert TEMPLATE["note"]["reversal"] in text
    assert TEMPLATE["note"]["search_failed"] in text
    assert TEMPLATE["sources"]["contradicting"] in text
    for url in (
        "https://x.com/anchoracct/status/100",
        "https://x.com/second/status/101",
        "https://x.com/third/status/102",
        "https://x.com/doubter/status/103",
    ):
        assert url in text and url in message.html
    # a repost names the original author, linked through the repost
    assert "@origin" in text and "@third" not in text


def test_no_ownership_managers_certainty_or_new_marker_in_a_digest():
    message = render_alert("digest", deadline(), AS_OF, [full_report()])
    for part in (message.text, message.html):
        for hidden in ("Jan Kowalski", "Kowalski FC", "Nowak XI", "23.4", MARKER):
            assert hidden not in part
    assert "czas warszawski" not in message.text


def test_notes_only_when_flagged():
    plain = report(
        listed(SAKA, managers=(ManagerRef("Jan Kowalski", "Kowalski FC"),)),
        corroboration(SAKA),
    )
    text = render_alert("news", deadline(), AS_OF, [plain]).text
    assert TEMPLATE["note"]["reversal"] not in text
    assert TEMPLATE["note"]["search_failed"] not in text
    failed = report(listed(SAKA), corroboration(SAKA), search_failed=True)
    assert (
        TEMPLATE["note"]["search_failed"] in render_alert("news", deadline(), AS_OF, [failed]).text
    )


def test_new_sources_are_marked_only_in_news():
    news = render_alert("news", deadline(), AS_OF, [full_report()])
    marked = [line for line in news.text.splitlines() if MARKER in line]
    assert len(marked) == 2
    assert {"status/100" in line or "status/102" in line for line in marked} == {True}
    bold = f'style="color:#37003c;{TEMPLATE["html"]["new_weight"]}"'
    assert news.html.count(bold) == 2
    for kind in ("digest", "breaking"):
        other = render_alert(kind, deadline(), AS_OF, [full_report()])
        assert MARKER not in other.text
        assert bold not in other.html


def test_groups_follow_the_event_order_with_a_summary():
    reports = [
        player_report(SAKA, "confirmed_starter"),
        player_report(ISAK, "doubt"),
        player_report(PALMER, "out"),
        player_report(HALL, "doubt"),
    ]
    message = render_alert("digest", deadline(), AS_OF, reports)
    text = message.text

    headings = [TEMPLATE["group"][event] for event in ("out", "doubt", "confirmed_starter")]
    positions = [text.index(heading) for heading in headings]
    assert positions == sorted(positions)
    assert TEMPLATE["group"]["benched"] not in text
    assert text.index("Palmer") < text.index("Isak") < text.index("Saka")
    assert f"{TEMPLATE['summary']['doubt']} 2" in text
    assert TEMPLATE["summary"]["benched"] not in text
    for heading in headings:
        assert heading in message.html


def test_within_a_group_most_accounts_first_then_overall_ownership():
    disputed = report(
        listed(PALMER, percent="1", claims=(300,)),
        corroboration(
            PALMER, anchor_x_id=300, contradicting=[citation(301, "x"), citation(302, "y")]
        ),
    )
    reports = [
        player_report(SAKA, supporters=0, percent="60"),
        player_report(ISAK, supporters=1, percent="5"),
        disputed,  # 1 for, 2 against: the most accounts in total
        player_report(HALL, supporters=0, percent="20"),
    ]
    text = render_alert("digest", deadline(), AS_OF, reports).text
    order = sorted(("Saka", "Isak", "Palmer", "Hall"), key=text.index)
    assert order == ["Palmer", "Isak", "Saka", "Hall"]


def test_unknown_event_type_is_left_out_and_the_rest_still_renders():
    odd = player_report(ISAK, "fit")
    message = render_alert("breaking", deadline(), AS_OF, [odd, player_report(SAKA)])
    assert "Isak" not in message.text and "Isak" not in message.title
    assert "Saka" in message.text and "Saka" in message.title


def test_a_report_without_an_anchor_is_left_out():
    empty = Corroboration(
        player=ISAK,
        as_of=AS_OF,
        window_start=AS_OF - timedelta(days=3),
        new_since=AS_OF - timedelta(days=3),
        anchor=None,
        retrieval=RetrievalReport("skipped", "no claim in the window"),
    )
    message = render_alert("breaking", deadline(), AS_OF, [report(listed(ISAK), empty)])
    assert "Isak" not in message.text and "Isak" not in message.title
    assert TEMPLATE["digest"]["empty"] in message.text


def test_empty_digest_says_so():
    message = render_alert("digest", deadline(), AS_OF, [])
    assert TEMPLATE["digest"]["empty"] in message.text
    assert TEMPLATE["digest"]["empty"] in message.html
    with_news = render_alert("digest", deadline(), AS_OF, [full_report()]).text
    assert TEMPLATE["digest"]["empty"] not in with_news


def test_low_grade_card_is_dashed():
    low = render_alert("digest", deadline(), AS_OF, [player_report(SAKA, level="low")]).html
    assert "5px dashed" in low and "5px solid" not in low
    medium = render_alert("digest", deadline(), AS_OF, [player_report(SAKA)]).html
    assert "5px solid" in medium


def test_news_of_the_last_24_hours_carries_a_badge():
    badge = TEMPLATE["age"]["fresh"]
    fresh = render_alert("digest", deadline(), AS_OF, [player_report(SAKA)])  # 90 minutes old
    assert badge in fresh.text and badge in fresh.html
    day_old = render_alert(
        "digest", deadline(), AS_OF + timedelta(hours=22, minutes=30), [player_report(SAKA)]
    )
    assert badge in day_old.text  # 24 hours old
    older = render_alert("digest", deadline(), AS_OF + timedelta(hours=23), [player_report(SAKA)])
    assert badge not in older.text and badge not in older.html


def test_news_of_the_last_24_hours_comes_first_within_a_group():
    old_but_backed = report(
        listed(ISAK, percent="60", claims=(200,)),
        replace(
            corroboration(ISAK, 200, supporting=[citation(201, "a"), citation(202, "b")]),
            anchor=Claim(
                post_ref(200, "anchoracct", AS_OF - timedelta(hours=30)), "doubt", "likely"
            ),
        ),
    )
    fresh_single = player_report(SAKA, percent="1")  # the anchor is 90 minutes old
    text = render_alert("digest", deadline(), AS_OF, [old_but_backed, fresh_single]).text
    assert text.index("Saka") < text.index("Isak")


def test_age_text():
    def age(delta: timedelta) -> str:
        return age_text(TEMPLATE, AS_OF - delta, AS_OF)

    assert age(timedelta(seconds=10)) == TEMPLATE["age"]["minutes"].format(count=1)
    assert age(timedelta(minutes=42)) == TEMPLATE["age"]["minutes"].format(count=42)
    assert age(timedelta(hours=5, minutes=50)) == TEMPLATE["age"]["hours"].format(count=5)
    # AS_OF is Sunday 16:00 in Warsaw; older news counts calendar days
    assert age(timedelta(hours=30)) == TEMPLATE["age"]["day"]  # Saturday 10:00
    assert age(timedelta(hours=41)) == TEMPLATE["age"]["days"].format(count=2)  # Friday 23:00
    assert age(timedelta(days=8, hours=3)) == TEMPLATE["age"]["days"].format(count=8)
    assert age(-timedelta(minutes=5)) == TEMPLATE["age"]["minutes"].format(count=1)


def test_accounts_use_the_matching_plural_form():
    def text(supporters: int) -> str:
        return render_alert(
            "digest", deadline(), AS_OF, [player_report(SAKA, supporters=supporters)]
        ).text

    assert TEMPLATE["accounts"]["one"] in text(0)
    assert TEMPLATE["accounts"]["few"].format(count=3) in text(2)
    assert TEMPLATE["accounts"]["many"].format(count=5) in text(4)


def test_html_escapes_values():
    odd = player_ref(9, "O'Brien <b>", "A&B")
    html = render_alert("digest", deadline(), AS_OF, [player_report(odd)]).html
    assert "O&#x27;Brien &lt;b&gt;" in html and "A&amp;B" in html
    assert "<b>" not in html.replace("<b>1</b>", "")


def test_plural_forms_follow_polish_rules():
    forms = {count: plural_form(count) for count in (1, 2, 3, 4, 5, 11, 12, 14, 21, 22, 25, 104)}
    assert forms == {
        1: "one",
        2: "few",
        3: "few",
        4: "few",
        5: "many",
        11: "many",
        12: "many",
        14: "many",
        21: "many",
        22: "few",
        25: "many",
        104: "few",
    }


def day_time(weekday: int, date: str, time: str) -> str:
    return TEMPLATE["header"]["day_time"].format(
        weekday=TEMPLATE["header"]["weekdays"][weekday], date=date, time=time
    )


def test_times_are_warsaw():
    as_of = datetime(2026, 10, 4, 14, 0, tzinfo=UTC)
    message = render_alert("digest", deadline(), as_of, [full_report()])
    moment = TEMPLATE["header"]["moment"].format(
        as_of="16:00", deadline=day_time(6, "04.10", "18:00")
    )
    assert moment in message.text and moment in message.html  # the deadline is 16:00 UTC
    assert POST_TIME.hour == 12 and "04.10 14:30" in message.text  # a post at 12:30 UTC


def test_alert_moment_on_another_day_than_the_deadline_shows_its_day():
    as_of = datetime(2026, 10, 3, 20, 30, tzinfo=UTC)  # Saturday 22:30 in Warsaw
    text = render_alert("digest", deadline(), as_of, []).text
    moment = TEMPLATE["header"]["moment"].format(
        as_of=day_time(5, "03.10", "22:30"), deadline=day_time(6, "04.10", "18:00")
    )
    assert moment in text


def test_rehearsal_title_names_the_rehearsal_moment():
    title = render_alert("digest", deadline(rehearsal=True), AS_OF, []).title
    assert "2026-10-04 18:00" in title
    assert "GW" not in title


def test_breaking_title_lists_the_players():
    title = render_alert("breaking", deadline(), AS_OF, [player_report(SAKA)]).title
    assert "GW6" in title and "Saka" in title


def test_alerts_code_has_no_polish_literals():
    polish = set("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")
    root = Path(__file__).resolve().parents[2] / "app" / "alerts"
    files = list(root.rglob("*.py"))
    assert files
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not polish & set(node.value), f"{path.name}: {node.value!r}"
