from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest

from app.adapters.ingestion import AdapterError, NormalizedDocument, RssHttpAdapter
from app.models import Source
from app.services import ingestion

FEED_URL = "https://public.example.test/feed.xml"
RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Public feed</title><item><title>Real public item</title><link>https://public.example.test/story</link><pubDate>Wed, 17 Sep 2026 12:00:00 GMT</pubDate><description>Public item summary.</description></item></channel></rss>"""


def _transport(status: int = 200, body: str = RSS):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=body, request=request)

    return httpx.MockTransport(handler)


def _source(**overrides) -> Source:
    values = {
        "id": uuid.uuid4(),
        "name": "Public RSS source",
        "canonical_url": "https://public.example.test",
        "feed_url": FEED_URL,
        "source_type": "RSS",
        "tier": "TIER_1",
        "is_active": True,
    }
    values.update(overrides)
    return Source(**values)


def test_http_rss_fetch_reuses_normalization() -> None:
    documents = asyncio.run(RssHttpAdapter(transport=_transport()).fetch(FEED_URL, source_id=uuid.uuid4()))

    assert len(documents) == 1
    assert documents[0].title == "Real public item"
    assert documents[0].canonical_url == "https://public.example.test/story"
    assert documents[0].normalized_content == "Public item summary."
    assert documents[0].metadata["adapter"] == "rss_http"


def test_empty_http_feed_returns_no_documents() -> None:
    empty = "<?xml version=\"1.0\"?><rss version=\"2.0\"><channel><title>Empty</title></channel></rss>"

    documents = asyncio.run(RssHttpAdapter(transport=_transport(body=empty)).fetch(FEED_URL, source_id=uuid.uuid4()))

    assert documents == []


def test_http_errors_are_safe() -> None:
    with pytest.raises(AdapterError, match="HTTP 503"):
        asyncio.run(RssHttpAdapter(transport=_transport(status=503)).fetch(FEED_URL, source_id=uuid.uuid4()))


def test_invalid_xml_is_safe() -> None:
    with pytest.raises(AdapterError, match="not valid XML"):
        asyncio.run(
            RssHttpAdapter(transport=_transport(body="<rss>")).fetch(
                FEED_URL, source_id=uuid.uuid4()
            )
        )


def test_timeout_is_safe() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(AdapterError, match="timed out"):
        asyncio.run(RssHttpAdapter(transport=httpx.MockTransport(handler)).fetch(FEED_URL, source_id=uuid.uuid4()))


def test_ingestion_selects_public_feed_url(monkeypatch) -> None:
    captured: dict[str, str] = {}

    class FakeHttpAdapter:
        async def fetch(self, feed_url: str, *, source_id, retrieved_at):
            captured["url"] = feed_url
            return []

    monkeypatch.setattr(ingestion, "RssHttpAdapter", FakeHttpAdapter)

    class Session:
        async def commit(self):
            pass

    result = asyncio.run(ingestion.ingest_source(Session(), _source()))

    assert captured["url"] == FEED_URL
    assert result.adapter == "rss_http"
    assert result.documents_found == 0
