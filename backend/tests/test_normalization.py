from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.adapters.ingestion import NormalizedDocument
from app.services.normalization import (
    content_hash,
    normalize_document,
    normalize_timestamp,
    normalize_url,
    normalize_whitespace,
)


def _document(**overrides) -> NormalizedDocument:
    values = {
        "source_id": uuid.uuid4(),
        "canonical_url": "HTTPS://Example.TEST/story/?utm_source=mail&id=7&fbclid=x",
        "title": "  A   synthetic\n title ",
        "published_at": datetime(2026, 9, 16, 12, tzinfo=timezone(timedelta(hours=2))),
        "retrieved_at": datetime(2026, 9, 16, 13, tzinfo=timezone.utc),
        "raw_content": "<raw> source </raw>",
        "normalized_content": "  body   with\nextra whitespace ",
        "content_type": "text/plain",
    }
    values.update(overrides)
    return NormalizedDocument(**values)


def test_url_normalization_removes_tracking_and_preserves_meaningful_query() -> None:
    assert normalize_url(
        "HTTPS://Example.TEST/story/?utm_source=mail&id=7&fbclid=x"
    ) == "https://example.test/story?id=7"


def test_whitespace_and_hash_are_deterministic() -> None:
    assert normalize_whitespace(" title\nwith   spaces ") == "title with spaces"
    assert content_hash(" body\nwith spaces ") == content_hash("body with   spaces")
    assert content_hash("body one") != content_hash("body two")


def test_document_normalization_preserves_raw_data_and_normalizes_timestamp() -> None:
    document = normalize_document(_document())

    assert document.original_url.startswith("HTTPS://Example.TEST")
    assert document.normalized_url == "https://example.test/story?id=7"
    assert document.title == "A synthetic title"
    assert document.normalized_content == "body with extra whitespace"
    assert document.published_at == datetime(2026, 9, 16, 10, tzinfo=timezone.utc)
    assert document.raw_content == "<raw> source </raw>"
    assert document.content_hash == content_hash("body with extra whitespace")


def test_naive_timestamp_is_interpreted_as_utc() -> None:
    value = datetime(2026, 9, 16, 10)
    assert normalize_timestamp(value) == value.replace(tzinfo=timezone.utc)