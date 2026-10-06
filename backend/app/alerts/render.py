import logging
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from functools import cache
from html import escape
from pathlib import Path
from typing import Any

from app.alerts.schemas import AlertDeadline, AlertKind, PlayerReport
from app.core.local_time import WARSAW, format_local
from app.corroboration.schemas import Citation
from app.delivery.channels.base import Message

logger = logging.getLogger(__name__)

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "content" / "alert_email.toml"

# Groups in the order they appear in the e-mail, one per anchor event type.
GROUP_ORDER = ("out", "doubt", "benched", "confirmed_starter")
# A card whose anchor post is older than this is drawn faded.
STALE_AFTER = timedelta(days=3)
POST_TIME = "%d.%m %H:%M"


@cache
def load_template() -> Mapping[str, Any]:
    return tomllib.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))


def plural_form(count: int) -> str:
    if count == 1:
        return "one"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "few"
    return "many"


def _local(moment: datetime, pattern: str) -> str:
    return moment.astimezone(WARSAW).strftime(pattern)


@dataclass(frozen=True)
class Source:
    account: str
    url: str
    created_at: datetime
    new: bool


@dataclass(frozen=True)
class Card:
    """One player's news with everything both e-mail parts show, computed once."""

    report: PlayerReport
    event: str
    level: str
    sources: tuple[Source, ...]
    contradicting: tuple[Source, ...]
    details: str
    notes: tuple[str, ...]
    stale: bool

    @property
    def accounts(self) -> int:
        return len(self.sources) + len(self.contradicting)


def age_text(template: Mapping[str, Any], created_at: datetime, as_of: datetime) -> str:
    """Minutes, then hours up to a day; older news by calendar day in Warsaw."""
    texts = template["age"]
    seconds = max((as_of - created_at).total_seconds(), 0)
    if seconds < 3600:
        return texts["minutes"].format(count=max(int(seconds // 60), 1))
    if seconds < 86400:
        return texts["hours"].format(count=int(seconds // 3600))
    days = (as_of.astimezone(WARSAW).date() - created_at.astimezone(WARSAW).date()).days
    return texts["day"] if days == 1 else texts["days"].format(count=days)


def _citation_source(citation: Citation, marked: frozenset[int]) -> Source:
    return Source(
        citation.original_author, citation.url, citation.created_at, citation.x_id in marked
    )


def _card(
    template: Mapping[str, Any], kind: AlertKind, report: PlayerReport, as_of: datetime
) -> Card | None:
    result = report.corroboration
    if result.anchor is None or result.grade is None:
        return None
    post = result.anchor.post
    marked = report.new_x_ids if kind == "news" else frozenset()
    sources = (
        Source(post.original_author, post.url, post.created_at, post.x_id in marked),
        *(_citation_source(c, marked) for c in result.supporting),
    )
    contradicting = tuple(_citation_source(c, marked) for c in result.contradicting)
    details = [
        template["grade"][result.grade.level],
        template["accounts"][plural_form(len(sources))].format(count=len(sources)),
    ]
    if contradicting:
        details.append(template["accounts"]["against"].format(count=len(contradicting)))
    details.append(age_text(template, post.created_at, as_of))
    notes = []
    if result.reversal:
        notes.append(template["note"]["reversal"])
    if report.search_failed or result.retrieval.failure is not None:
        notes.append(template["note"]["search_failed"])
    return Card(
        report=report,
        event=result.anchor.event_type,
        level=result.grade.level,
        sources=sources,
        contradicting=contradicting,
        details=template["grade"]["separator"].join(details),
        notes=tuple(notes),
        stale=as_of - post.created_at > STALE_AFTER,
    )


def _order_key(card: Card) -> tuple[int, bool, Decimal, str]:
    percent = card.report.listed.selected_by_percent
    return (
        -card.accounts,
        percent is None,
        -(percent or Decimal(0)),
        card.report.listed.player.web_name,
    )


def group_cards(
    template: Mapping[str, Any], kind: AlertKind, reports: Sequence[PlayerReport], as_of: datetime
) -> dict[str, list[Card]]:
    """Cards by anchor event type in GROUP_ORDER; within a group the player with the most
    independent accounts (for and against) first, ties broken by overall ownership. A report
    with no anchor, or with an event type the e-mail has no group for, is left out."""
    groups: dict[str, list[Card]] = {event: [] for event in GROUP_ORDER}
    for report in reports:
        card = _card(template, kind, report, as_of)
        if card is None:
            continue
        if card.event not in groups:
            logger.warning("alert card skipped: no group for event type %s", card.event)
            continue
        groups[card.event].append(card)
    for cards in groups.values():
        cards.sort(key=_order_key)
    return {event: cards for event, cards in groups.items() if cards}


def _deadline_label(template: Mapping[str, Any], deadline: AlertDeadline) -> str:
    texts = template["deadline_label"]
    if deadline.rehearsal:
        return texts["rehearsal"].format(time=format_local(deadline.deadline_at))
    return texts["gameweek"].format(gameweek=deadline.gameweek)


def _day_time(template: Mapping[str, Any], moment: datetime) -> str:
    local = moment.astimezone(WARSAW)
    return template["header"]["day_time"].format(
        weekday=template["header"]["weekdays"][local.weekday()],
        date=local.strftime("%d.%m"),
        time=local.strftime("%H:%M"),
    )


def _moment(template: Mapping[str, Any], deadline: AlertDeadline, as_of: datetime) -> str:
    same_day = as_of.astimezone(WARSAW).date() == deadline.deadline_at.astimezone(WARSAW).date()
    return template["header"]["moment"].format(
        as_of=_local(as_of, "%H:%M") if same_day else _day_time(template, as_of),
        deadline=_day_time(template, deadline.deadline_at),
    )


def _summary_items(
    template: Mapping[str, Any], groups: Mapping[str, list[Card]]
) -> list[tuple[str, int]]:
    return [(template["summary"][event], len(cards)) for event, cards in groups.items()]


def _source_groups(template: Mapping[str, Any], card: Card) -> list[tuple[str, tuple[Source, ...]]]:
    """The labelled source lists of a card: its sources, then the contradicting ones if any."""
    groups = [(template["sources"]["one" if len(card.sources) == 1 else "many"], card.sources)]
    if card.contradicting:
        groups.append((template["sources"]["contradicting"], card.contradicting))
    return groups


def _render_text(
    template: Mapping[str, Any],
    headline: str,
    moment: str,
    groups: Mapping[str, list[Card]],
) -> str:
    texts = template["text"]
    lines = [headline, moment]
    if groups:
        lines.append(
            template["summary"]["separator"].join(
                f"{label} {count}" for label, count in _summary_items(template, groups)
            )
        )
    else:
        lines.extend(["", template["digest"]["empty"]])
    for event, cards in groups.items():
        lines.extend(["", template["group"][event]])
        for card in cards:
            player = card.report.listed.player
            club = texts["club"].format(club=player.team_name) if player.team_name else ""
            lines.append("")
            lines.append(
                texts["player"].format(
                    name=player.web_name, club=club, event=template["event"][event]
                )
            )
            lines.append(card.details)
            lines.extend(card.notes)
            for label, sources in _source_groups(template, card):
                lines.append(texts["sources"].format(label=label))
                lines.extend(
                    texts["source"].format(
                        marker=template["sources"]["new_marker"] if source.new else "",
                        account=source.account,
                        time=_local(source.created_at, POST_TIME),
                        url=source.url,
                    )
                    for source in sources
                )
    return "\n".join(lines)


def _links(template: Mapping[str, Any], sources: Sequence[Source]) -> str:
    """Linked accounts; the first carries its post's time."""
    html = template["html"]
    links = [
        html["source_link"].format(
            marker=escape(template["sources"]["new_marker"]) if source.new else "",
            url=escape(source.url),
            account=escape(source.account),
        )
        for source in sources
    ]
    links[0] = html["source_first"].format(
        link=links[0], time=escape(_local(sources[0].created_at, POST_TIME))
    )
    return html["source_separator"].join(links)


def _render_card(template: Mapping[str, Any], card: Card) -> str:
    html = template["html"]
    palette = template["palette"][card.event]
    stale = template["palette"]["stale"]
    player = card.report.listed.player
    sources = html["line_break"].join(
        html["sources"].format(label=escape(label), links=_links(template, items))
        for label, items in _source_groups(template, card)
    )
    return html["card"].format(
        border="dashed" if card.level == "low" else "solid",
        accent=palette["accent"],
        background=stale["background"] if card.stale else palette["background"],
        name_color=stale["text"] if card.stale else template["palette"]["base"]["text"],
        detail_color=stale["text"] if card.stale else palette["heading"],
        name=escape(player.web_name),
        club=escape(player.team_name or ""),
        event=escape(template["event"][card.event]),
        details=escape(card.details),
        notes="".join(html["note"].format(text=escape(note)) for note in card.notes),
        sources=sources,
    )


def _render_html(
    template: Mapping[str, Any],
    kind: AlertKind,
    title: str,
    headline: str,
    moment: str,
    groups: Mapping[str, list[Card]],
) -> str:
    html = template["html"]
    body = []
    if groups:
        summary = template["summary"]["separator"].join(
            html["summary_item"].format(label=escape(label), count=count)
            for label, count in _summary_items(template, groups)
        )
        body.append(
            html["summary"].format(summary=summary, legend=escape(template["grade"]["legend"]))
        )
        for event, cards in groups.items():
            body.append(
                html["group"].format(
                    color=template["palette"][event]["heading"],
                    heading=escape(template["group"][event]),
                )
            )
            body.extend(_render_card(template, card) for card in cards)
        body.append(html["spacer"])
    else:
        body.append(html["empty"].format(text=escape(template["digest"]["empty"])))
    return html["document"].format(
        title=escape(title),
        kind=escape(template["kind"][kind]),
        headline=escape(headline),
        moment=escape(moment),
        body="\n".join(body),
        footer=escape(template["footer"]["text"]),
    )


def render_alert(
    kind: AlertKind,
    deadline: AlertDeadline,
    as_of: datetime,
    reports: Sequence[PlayerReport],
    template: Mapping[str, Any] | None = None,
) -> Message:
    template = template if template is not None else load_template()
    groups = group_cards(template, kind, reports, as_of)
    deadline_label = _deadline_label(template, deadline)
    # the title names only the players the body shows
    names = ", ".join(
        card.report.listed.player.web_name for cards in groups.values() for card in cards
    )
    title = template["title"][kind].format(deadline_label=deadline_label, players=names)
    headline = template["header"]["headline"].format(deadline_label=deadline_label)
    moment = _moment(template, deadline, as_of)
    return Message(
        title=title,
        text=_render_text(template, headline, moment, groups),
        html=_render_html(template, kind, title, headline, moment, groups),
    )
