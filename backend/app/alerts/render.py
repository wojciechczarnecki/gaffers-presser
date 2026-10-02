import tomllib
from collections.abc import Mapping, Sequence
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any

from app.alerts.schemas import AlertDeadline, AlertKind, ListedPlayer, PlayerReport
from app.core.local_time import format_local
from app.corroboration.schemas import Citation
from app.delivery.channels.base import Message

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "content" / "alert_email.toml"


@cache
def load_template() -> Mapping[str, Any]:
    return tomllib.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))


def _account(template: Mapping[str, Any], author: str, original: str | None) -> str:
    if original:
        return template["link"]["repost"].format(author=author, original=original)
    return template["link"]["account"].format(author=author)


def _categories(template: Mapping[str, Any], listed: ListedPlayer) -> str:
    texts = template["category"]
    parts = []
    if listed.managers:
        managers = ", ".join(
            texts["manager"].format(manager=m.manager_name, team=m.team_name)
            for m in listed.managers
        )
        parts.append(texts["league_owned"].format(managers=managers))
    if listed.widely_owned and listed.selected_by_percent is not None:
        parts.append(texts["widely_owned"].format(percent=listed.selected_by_percent))
    if listed.trending_accounts is not None:
        parts.append(texts["trending"].format(count=listed.trending_accounts))
    return texts["separator"].join(parts)


def _citation_line(
    template: Mapping[str, Any], role: str, citation: Citation, new_x_ids: frozenset[int]
) -> str:
    marker = template["link"]["new_marker"] if citation.x_id in new_x_ids else ""
    return template["link"][role].format(
        marker=marker,
        account=_account(template, citation.author_handle, citation.reposted_author_handle),
        time=format_local(citation.created_at),
        url=citation.url,
    )


def _section(template: Mapping[str, Any], report: PlayerReport) -> list[str]:
    listed = report.listed
    player = listed.player
    club = f" ({player.team_name})" if player.team_name else ""
    lines = [
        f"{player.web_name}{club}",
        template["section"]["categories"].format(categories=_categories(template, listed)),
    ]
    result = report.corroboration
    anchor = result.anchor
    if anchor is None or result.grade is None:
        return [*lines, template["section"]["no_anchor"]]
    lines.append(
        template["section"]["news"].format(
            event=template["event"][anchor.event_type],
            certainty=template["certainty"][anchor.certainty],
        )
    )
    lines.append(template["section"]["grade"].format(grade=template["grade"][result.grade.level]))
    lines.append(
        template["section"]["accounts"].format(
            supporting=len(result.supporting), contradicting=len(result.contradicting)
        )
    )
    if result.reversal:
        lines.append(template["note"]["reversal"])
    if report.search_failed or result.retrieval.failure is not None:
        lines.append(template["note"]["search_failed"])
    lines.append(template["section"]["links"])
    post = anchor.post
    marker = template["link"]["new_marker"] if post.x_id in report.new_x_ids else ""
    lines.append(
        "  "
        + template["link"]["anchor"].format(
            marker=marker,
            account=_account(
                template,
                post.author_handle,
                post.reposted_author_handle if post.is_repost else None,
            ),
            time=format_local(post.created_at),
            url=template["link"]["anchor_url"].format(author=post.author_handle, x_id=post.x_id),
        )
    )
    for role, citations in (
        ("supporting", result.supporting),
        ("contradicting", result.contradicting),
    ):
        lines.extend(
            "  " + _citation_line(template, role, citation, report.new_x_ids)
            for citation in citations
        )
    return lines


def _deadline_label(template: Mapping[str, Any], deadline: AlertDeadline) -> str:
    texts = template["deadline_label"]
    if deadline.rehearsal:
        return texts["rehearsal"].format(time=format_local(deadline.deadline_at))
    return texts["gameweek"].format(gameweek=deadline.gameweek)


def render_alert(
    kind: AlertKind,
    deadline: AlertDeadline,
    as_of: datetime,
    reports: Sequence[PlayerReport],
    listed_without_news: int,
    template: Mapping[str, Any] | None = None,
) -> Message:
    template = template if template is not None else load_template()
    names = ", ".join(report.listed.player.web_name for report in reports)
    title = template["title"][kind].format(
        deadline_label=_deadline_label(template, deadline), players=names
    )
    lines = [
        template["intro"][kind],
        template["header"]["as_of"].format(as_of=format_local(as_of)),
        template["header"]["deadline"].format(deadline=format_local(deadline.deadline_at)),
    ]
    for report in reports:
        lines.append("")
        lines.extend(_section(template, report))
    if kind == "digest":
        lines.append("")
        if not reports:
            lines.append(template["digest"]["empty"])
        if listed_without_news:
            lines.append(template["digest"]["no_news"].format(count=listed_without_news))
    return Message(title=title, text="\n".join(lines))
