import re
from html import unescape
from urllib.parse import unquote

from app.presser.render import render_presser

PRESSER = '🎙️ *Presser* <GW6> & spółka\n\nDrugi akapit: 100% "pewne".'


def test_title_text_and_whatsapp_button():
    message = render_presser("Liga & Spółka", 6, PRESSER)
    assert message.title == "Presser GW6 — Liga & Spółka"
    assert message.text == PRESSER
    assert message.html is not None
    assert "&lt;GW6&gt; &amp; spółka" in message.html
    assert "Liga &amp; Spółka" in message.html
    assert "<GW6>" not in message.html
    (href,) = re.findall(r'href="(https://wa\.me/\?text=[^"]*)"', message.html)
    assert unquote(unescape(href).removeprefix("https://wa.me/?text=")) == PRESSER
    assert "Wyślij na WhatsApp" in message.html


def test_paragraphs_and_line_breaks():
    message = render_presser("L", 1, "a\nb\n\nc")
    assert message.html is not None
    assert '<p style="margin:0 0 12px;">a<br>b</p>' in message.html
    assert message.html.count("<p style") == 2
