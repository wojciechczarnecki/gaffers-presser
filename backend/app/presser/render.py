import tomllib
from collections.abc import Mapping
from functools import cache
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.delivery.channels.base import Message

TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "content" / "presser_email.toml"
WHATSAPP_SHARE_URL = "https://wa.me/?text={text}"


@cache
def load_template() -> Mapping[str, Any]:
    return tomllib.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))


def _paragraphs(template: Mapping[str, Any], text: str) -> str:
    html = template["html"]
    blocks = [block for block in text.strip().split("\n\n") if block.strip()]
    return "".join(
        html["paragraph"].format(
            text=html["line_break"].join(escape(line) for line in block.strip().split("\n"))
        )
        for block in blocks
    )


def render_presser(league_name: str, gameweek: int, text: str) -> Message:
    template = load_template()
    html = template["html"]
    title = template["title"]["presser"].format(gameweek=gameweek, league=league_name)
    url = WHATSAPP_SHARE_URL.format(text=quote(text, safe=""))
    share = html["share"].format(url=escape(url), label=escape(template["whatsapp"]["button"]))
    header = template["header"]
    document = html["document"].format(
        title=escape(title),
        kicker=escape(header["kicker"]),
        headline=escape(header["headline"].format(gameweek=gameweek)),
        league=escape(header["league"].format(league=league_name)),
        body=html["body"].format(paragraphs=_paragraphs(template, text)),
        share=share,
        footer=escape(template["footer"]["text"]),
    )
    return Message(title=title, text=text, html=document)
