from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.adapters.ingestion import NormalizedDocument

_TRACKING_KEYS = {
    "fbclid", "gclid", "dclid", "mc_cid", "mc_eid", "igshid", "ref_src", "cmpid", "spm", "msclkid",
    "yclid", "gbraid", "wbraid", "_ga", "_gl",
}
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class NormalizedArticle:
    source_id: uuid.UUID
    original_url: str
    normalized_url: str
    title: str | None
    published_at: datetime | None
    fetched_at: datetime
    raw_content: str
    normalized_content: str
    content_hash: str | None


def normalize_whitespace(value: str | None) -> str | None:
    if value is None:
        return None
    return _WHITESPACE.sub(" ", value).strip()


def normalize_url(value: str) -> str:
    value = value.strip()
    try:
        parsed = urlsplit(value)
        parsed_port = parsed.port
    except ValueError:
        return value
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return value.strip()

    netloc = hostname
    if parsed_port is not None and not (
        (scheme == "http" and parsed_port == 80)
        or (scheme == "https" and parsed_port == 443)
    ):
        netloc = f"{netloc}:{parsed_port}"

    path = parsed.path or "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"

    query = [
        (key, query_value)
        for key, query_value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_KEYS
    ]
    query.sort()
    return urlunsplit((scheme, netloc, path, urlencode(query, doseq=True), ""))


def normalize_timestamp(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def content_hash(value: str | None) -> str | None:
    normalized = normalize_whitespace(value)
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def normalize_document(document: NormalizedDocument) -> NormalizedArticle:
    normalized_content = normalize_whitespace(document.normalized_content) or ""
    return NormalizedArticle(
        source_id=document.source_id,
        original_url=document.canonical_url,
        normalized_url=normalize_url(document.canonical_url),
        title=normalize_whitespace(document.title),
        published_at=normalize_timestamp(document.published_at),
        fetched_at=normalize_timestamp(document.retrieved_at)
        or datetime.now(timezone.utc),
        raw_content=document.raw_content,
        normalized_content=normalized_content,
        content_hash=content_hash(normalized_content),
    )