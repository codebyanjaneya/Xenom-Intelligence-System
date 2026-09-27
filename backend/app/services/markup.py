"""HTML/RSS residue detection for display fields and evidence quotes."""
from __future__ import annotations

import html
import re

_MARKUP = re.compile(r"<\s*/?\s*[a-zA-Z!][^>]{0,200}>|&lt;|&gt;|\bclass=|\bhref=")


def has_markup(text: str | None) -> bool:
    """HTML/RSS residue (tags, escaped tags, attributes). Never shown as evidence or as a display field."""
    return bool(text) and bool(_MARKUP.search(text))


def strip_markup(text: str | None) -> str | None:
    """Display-safe copy of a title/summary: unchanged when clean, otherwise tags removed and entities decoded."""
    if not text or not has_markup(text):
        return text
    plain = re.sub(r"<[^>]*>", " ", html.unescape(html.unescape(text)))
    return " ".join(plain.split())
