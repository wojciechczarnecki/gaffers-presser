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
from app.corroboration.schemas import Citation, Claim, Grade
from app.delivery.channels.base import Message

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "content" / "alert_email.toml"

# Groups in the order they appear in the e-mail, one per anchor event type.
GROUP_ORDER = ("out", "doubt", "benched", "confirmed_starter")
# A card whose anchor post is older than this is drawn faded.
STALE_AFTER = timedelta(days=3)


@cache
def load_template() -> Mapping[str, Any]:
    return tomllib.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))


def plural_form(count: int) -> str:
    if count == 1:
        return "one"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return "few"
    return "many"


@dataclass(frozen=True)
class Source:
    account: str
    url: str
    created_at: datetime
    new: bool


@dataclass(frozen=True)
class Card:
    report: PlayerReport
    anchor: Claim
    grade: Grade
    sources: tuple[Source, ...]
    contradicting: tuple[Source, ...]

    @property
    def accounts(self) -> int:
        return len(self.sources) + len(self.contradicting)


def _citation_source(citation: Citation, new: bool) -> Source:
    account = citation.reposted_author_handle or citation.author_handle
    return Source(account, citation.url, citation.created_at, new)


def _card(template: Mapping[str, Any], kind: AlertKind, report: PlayerReport) -> Card | None:
    result = report.corroboration
    if result.anchor is None or result.grade is None:
        return None
    post = result.anchor.post
    marked = report.new_x_ids if kind == "news" else frozenset()
    account = post.reposted_author_handle if post.is_repost else None
    anchor = Source(
        account or post.author_handle,
        template["sources"]["anchor_url"].format(author=post.author_handle, x_id=post.x_id),
        post.created_at,
        post.x_id in marked,
    )
    return Card(
        report=report,
        anchor=result.anchor,
        grade=result.grade,
        sources=(
            anchor,
            *(_citation_source(c, c.x_id in marked) for c in result.supporting),
        ),
        contradicting=tuple(_citation_source(c, c.x_id in marked) for c in result.contradicting),
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
    template: Mapping[str, Any], kind: AlertKind, reports: Sequence[PlayerReport]
) -> dict[str, list[Card]]:
    """Cards by anchor event type in GROUP_ORDER; within a group the player with the most
    independent accounts first, ties broken by overall ownership."""
    groups: dict[str, list[Card]] = {event: [] for event in GROUP_ORDER}
    for report in reports:
        card = _card(template, kind, report)
        if card is not None:
            groups[card.anchor.event_type].append(card)
    for cards in groups.values():
        cards.sort(key=_order_key)
    return {event: cards for event, cards in groups.items() if cards}


def _local(moment: datetime, pattern: str) -> str:
    return moment.astimezone(WARSAW).strftime(pattern)


def age_text(template: Mapping[str, Any], created_at: datetime, as_of: datetime) -> str:
    texts = template["age"]
    seconds = max((as_of - created_at).total_seconds(), 0)
    if seconds < 3600:
        return texts["minutes"].format(count=max(int(seconds // 60), 1))
    if seconds < 86400:
        return texts["hours"].format(count=int(seconds // 3600))
    days = int(seconds // 86400)
    return texts["day"] if days == 1 else texts["days"].format(count=days)


def _details(template: Mapping[str, Any], card: Card, as_of: datetime) -> str:
    accounts = template["accounts"][plural_form(card.accounts)].format(count=card.accounts)
    return template["grade"]["separator"].join(
        (
            template["grade"][card.grade.level],
            accounts,
            age_text(template, card.anchor.post.created_at, as_of),
        )
    )


def _notes(template: Mapping[str, Any], card: Card) -> list[str]:
    notes = []
    if card.report.corroboration.reversal:
        notes.append(template["note"]["reversal"])
    if card.report.search_failed or card.report.corroboration.retrieval.failure is not None:
        notes.append(template["note"]["search_failed"])
    return notes


def _stale(card: Card, as_of: datetime) -> bool:
    return as_of - card.anchor.post.created_at > STALE_AFTER


def _deadline_label(template: Mapping[str, Any], deadline: AlertDeadline) -> str:
    texts = template["deadline_label"]
    if deadline.rehearsal:
        return texts["rehearsal"].format(time=format_local(deadline.deadline_at))
    return texts["gameweek"].format(gameweek=deadline.gameweek)


def _moment(template: Mapping[str, Any], deadline: AlertDeadline, as_of: datetime) -> str:
    local = deadline.deadline_at.astimezone(WARSAW)
    return template["header"]["moment"].format(
        as_of=_local(as_of, "%H:%M"),
        weekday=template["header"]["weekdays"][local.weekday()],
        date=local.strftime("%d.%m"),
        time=local.strftime("%H:%M"),
    )


def _summary_items(
    template: Mapping[str, Any], groups: Mapping[str, list[Card]]
) -> list[tuple[str, int]]:
    return [(template["summary"][event], len(cards)) for event, cards in groups.items()]


def _render_text(
    template: Mapping[str, Any],
    headline: str,
    moment: str,
    groups: Mapping[str, list[Card]],
    as_of: datetime,
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
            lines.append(_details(template, card, as_of))
            lines.extend(_notes(template, card))
            for label, sources in (
                (template["sources"]["one" if len(card.sources) == 1 else "many"], card.sources),
                (template["sources"]["contradicting"], card.contradicting),
            ):
                if not sources:
                    continue
                lines.append(texts["sources"].format(label=label))
                lines.extend(
                    texts["source"].format(
                        marker=template["sources"]["new_marker"] if source.new else "",
                        account=source.account,
                        time=_local(source.created_at, "%d.%m %H:%M"),
                        url=source.url,
                    )
                    for source in sources
                )
    return "\n".join(lines)


def _links(template: Mapping[str, Any], sources: Sequence[Source], with_time: bool) -> str:
    html = template["html"]
    links = []
    for index, source in enumerate(sources):
        link = html["source_link"].format(
            marker=escape(template["sources"]["new_marker"]) if source.new else "",
            url=escape(source.url),
            account=escape(source.account),
        )
        if index == 0 and with_time:
            link = html["source_first"].format(
                link=link, time=escape(_local(source.created_at, "%d.%m %H:%M"))
            )
        links.append(link)
    return html["source_separator"].join(links)


def _render_card(template: Mapping[str, Any], event: str, card: Card, as_of: datetime) -> str:
    html = template["html"]
    palette = template["palette"][event]
    stale = template["palette"]["stale"]
    is_stale = _stale(card, as_of)
    player = card.report.listed.player
    sources = html["sources"].format(
        label=escape(template["sources"]["one" if len(card.sources) == 1 else "many"]),
        links=_links(template, card.sources, with_time=True),
    )
    if card.contradicting:
        sources += html["line_break"] + html["sources"].format(
            label=escape(template["sources"]["contradicting"]),
            links=_links(template, card.contradicting, with_time=True),
        )
    return html["card"].format(
        border="dashed" if card.grade.level == "low" else "solid",
        accent=palette["accent"],
        background=stale["background"] if is_stale else palette["background"],
        name_color=stale["text"] if is_stale else template["palette"]["base"]["text"],
        detail_color=stale["text"] if is_stale else palette["heading"],
        name=escape(player.web_name),
        club=escape(player.team_name or ""),
        event=escape(template["event"][event]),
        details=escape(_details(template, card, as_of)),
        notes="".join(html["note"].format(text=escape(note)) for note in _notes(template, card)),
        sources=sources,
    )


def _render_html(
    template: Mapping[str, Any],
    kind: AlertKind,
    title: str,
    headline: str,
    moment: str,
    groups: Mapping[str, list[Card]],
    as_of: datetime,
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
            body.extend(_render_card(template, event, card, as_of) for card in cards)
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
    deadline_label = _deadline_label(template, deadline)
    names = ", ".join(report.listed.player.web_name for report in reports)
    title = template["title"][kind].format(deadline_label=deadline_label, players=names)
    headline = template["header"]["headline"].format(deadline_label=deadline_label)
    moment = _moment(template, deadline, as_of)
    groups = group_cards(template, kind, reports)
    return Message(
        title=title,
        text=_render_text(template, headline, moment, groups, as_of),
        html=_render_html(template, kind, title, headline, moment, groups, as_of),
    )
