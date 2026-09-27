from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.adapters.ingestion import AdapterError, HtmlFixtureAdapter, RssFixtureAdapter

FIXTURES = Path(__file__).parents[1] / "app" / "adapters" / "ingestion" / "fixtures"
SOURCE_ID = uuid.uuid4()
SOURCE_URL = "https://fixture.test/source"


def test_rss_fixture_creates_multiple_documents() -> None:
    documents = RssFixtureAdapter().parse_file(
        FIXTURES / "m4-example-feed.xml",
        source_id=SOURCE_ID,
        source_url=SOURCE_URL,
        retrieved_at=datetime(2026, 9, 16, tzinfo=timezone.utc),
    )

    assert len(documents) == 2
    assert documents[0].title == "JCB Construction Equipment Company Launches Revolutionary Excavator Technology"
    assert documents[0].canonical_url == "https://fixture.test/articles/one"
    assert documents[0].published_at == datetime(2026, 9, 15, 9, 30, tzinfo=timezone.utc)
    assert documents[1].published_at == datetime(2026, 9, 16, 10, 0, tzinfo=timezone.utc)
    assert documents[0].source_id == SOURCE_ID


def test_html_fixture_extracts_article_content_without_noise() -> None:
    document = HtmlFixtureAdapter().parse_file(
        FIXTURES / "m4-example-article.html",
        source_id=SOURCE_ID,
        source_url=SOURCE_URL,
    )[0]

    assert document.title == "Fixture article page"
    assert document.canonical_url == SOURCE_URL
    assert "synthetic article body" in document.normalized_content
    assert "Navigation noise" not in document.normalized_content
    assert "secretNoise" not in document.normalized_content
    assert document.published_at == datetime(2026, 9, 15, 10, tzinfo=timezone.utc)


def test_malformed_rss_is_reported_as_adapter_error() -> None:
    with pytest.raises(AdapterError, match="not valid XML"):
        RssFixtureAdapter().parse_file(
            FIXTURES / "m4-malformed-feed.xml",
            source_id=SOURCE_ID,
            source_url=SOURCE_URL,
        )


def test_partial_rss_entry_keeps_optional_fields_empty() -> None:
    raw = "<rss><channel><item><title>Only title</title></item></channel></rss>"

    document = RssFixtureAdapter().parse(
        raw,
        source_id=SOURCE_ID,
        source_url=SOURCE_URL,
    )[0]

    assert document.title == "Only title"
    assert document.canonical_url == SOURCE_URL
    assert document.published_at is None
    assert document.normalized_content == "Only title"
