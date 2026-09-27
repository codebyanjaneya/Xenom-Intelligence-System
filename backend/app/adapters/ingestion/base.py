from __future__ import annotations

import re
import unicodedata
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import unescape
from typing import Any


class AdapterError(ValueError):
    """Raised when a fixture cannot be interpreted by its adapter."""


@dataclass(frozen=True)
class NormalizedDocument:
    source_id: uuid.UUID
    canonical_url: str
    title: str | None
    published_at: datetime | None
    retrieved_at: datetime
    raw_content: str
    normalized_content: str
    content_type: str
    metadata: dict[str, Any] = field(default_factory=dict)


class IngestionAdapter(ABC):
    content_type: str

    @abstractmethod
    def parse(
        self,
        raw_content: str,
        *,
        source_id: uuid.UUID,
        source_url: str,
        retrieved_at: datetime | None = None,
    ) -> list[NormalizedDocument]:
        raise NotImplementedError


_MOJIBAKE_MARKERS = ("â€", "Ã¢", "Ã©", "Ã¨", "Â ", "Ã")
_MOJIBAKE_RUN = re.compile(
    "[Â-ô][-¿ŒœŠšŸŽžƒˆ˜"
    "–—‘-„†-•…‰‹›€™]+"
)
_INVISIBLE = dict.fromkeys(
    [0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x00AD] + list(range(0x00, 0x09)) + [0x0B, 0x0C] + list(range(0x0E, 0x20)),
    None,
)
_BOILERPLATE = re.compile(
    r"^(advertisement|advertisements|subscribe( to (our )?newsletters?)?|sign up for.*|follow us on.*|share this.*|"
    r"read more:?.*|also read:?.*|click here.*|skip to (main )?content|cookie(s)? (policy|settings).*|"
    r"we use cookies.*|all rights reserved.*|copyright ©.*|©.*|related (articles|news|stories):?.*|"
    r"join (our|us on) (whatsapp|telegram).*|download the app.*)$",
    re.I,
)


def fix_mojibake(value: str) -> str:
    """Repair UTF-8 text that was decoded as cp1252/latin-1 (e.g. 'â€™' -> '’')."""
    if not any(marker in value for marker in _MOJIBAKE_MARKERS):
        return value

    def _repair(match: re.Match[str]) -> str:
        run = match.group(0)
        raw = bytearray()
        for ch in run:
            try:
                raw += ch.encode("cp1252")
            except UnicodeEncodeError:
                if ord(ch) > 0xFF:
                    return run
                raw.append(ord(ch))  # C1 control (e.g. 0x9d) that cp1252 leaves undefined
        try:
            return bytes(raw).decode("utf-8")
        except UnicodeDecodeError:
            return run

    # Repair only the mojibake runs so already-correct characters in the same string survive.
    return _MOJIBAKE_RUN.sub(_repair, value)


def clean_text(value: str) -> str:
    """HTML -> plain text: drop script/style/comments, unescape, repair Unicode, collapse whitespace."""
    value = re.sub(r"<!--.*?-->", " ", value, flags=re.S)
    value = re.sub(r"<(script|style|noscript)\b[^>]*>.*?</\1\s*>", " ", value, flags=re.I | re.S)
    value = re.sub(r"</?(br|p|div|li|tr|h[1-6])\b[^>]*>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = unescape(value)
    value = fix_mojibake(value)
    value = unicodedata.normalize("NFC", value).translate(_INVISIBLE)
    return " ".join(value.split())


def clean_article_text(paragraphs: list[str], max_chars: int = 6000) -> str:
    """Join paragraphs after removing boilerplate lines and repeated paragraphs."""
    seen: set[str] = set()
    kept: list[str] = []
    for raw in paragraphs:
        para = clean_text(raw)
        if len(para) < 2 or _BOILERPLATE.match(para):
            continue
        key = para.lower()
        if key in seen:
            continue
        seen.add(key)
        kept.append(para)
    return " ".join(kept)[:max_chars]


def retrieval_time(value: datetime | None) -> datetime:
    return value or datetime.now(timezone.utc)
