"""Google News RSS feed fetcher for Phase-1 intelligence discovery."""

from __future__ import annotations

import logging
import asyncio
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote_plus, urlparse

import httpx

from app.adapters.ingestion.base import AdapterError, NormalizedDocument, clean_text

logger = logging.getLogger(__name__)

GOOGLE_NEWS_BASE_URL = "https://news.google.com/rss/search"


class GoogleNewsAdapter:
    """Fetches and parses Google News RSS search feeds for targeted company intelligence.

    Google's ``<link>`` is a wrapper URL. It is kept as the document URL here and
    resolved to the real publisher URL later, only for articles that pass the
    relevance gate (see ``google_news_decoder``). ``<source url=...>`` gives the
    publisher domain, which the relevance engine uses as a source-trust signal.
    """

    _request_lock = asyncio.Lock()
    _last_request_at = 0.0
    _min_request_interval = 0.75

    async def fetch(
        self,
        query: str,
        source_id: str,
        retrieved_at: datetime,
        when: str = "7d",
        timeout_sec: int = 10,
        region: str = "IN",
        language: str = "en",
    ) -> list[NormalizedDocument]:
        url = (
            f"{GOOGLE_NEWS_BASE_URL}?q={quote_plus(query + ' when:' + when)}"
            f"&hl={language}-{region}&gl={region}&ceid={region}:{language}"
        )
        for attempt in range(3):
            try:
                async with self._request_lock:
                    wait = self._min_request_interval - (time.monotonic() - self._last_request_at)
                    if wait > 0:
                        await asyncio.sleep(wait)
                    self._last_request_at = time.monotonic()
                async with httpx.AsyncClient(timeout=timeout_sec, follow_redirects=True) as client:
                    response = await client.get(url, headers={"Accept-Encoding": "gzip, deflate"})
                    if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                        retry_after = response.headers.get("retry-after")
                        delay = float(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt
                        await asyncio.sleep(min(delay, 15))
                        continue
                    response.raise_for_status()
                    content = response.text
                break
            except httpx.HTTPError as exc:
                if attempt == 2:
                    raise AdapterError(f"Google News RSS fetch failed for query '{query}': {exc}") from exc
                await asyncio.sleep(2 ** attempt)
        else:
            raise AdapterError(f"Google News RSS fetch failed for query '{query}'")
        logger.debug("Fetched Google News RSS for query %r: %d bytes", query, len(content))
        return self._parse_feed(content, source_id, retrieved_at, query)

    def _parse_feed(self, content: str, source_id: str, retrieved_at: datetime, query: str) -> list[NormalizedDocument]:
        try:
            root = ET.fromstring(content)
        except ET.ParseError as exc:
            raise AdapterError(f"Google News RSS XML parsing failed: {exc}") from exc

        documents: list[NormalizedDocument] = []
        for item_elem in root.findall(".//item"):
            doc = self._parse_item(item_elem, source_id, retrieved_at, query)
            if doc:
                documents.append(doc)
        logger.info("Parsed %d articles from Google News query %r", len(documents), query)
        return documents

    def _parse_item(self, item_elem: Any, source_id: str, retrieved_at: datetime, query: str) -> NormalizedDocument | None:
        title_elem = item_elem.find("title")
        link_elem = item_elem.find("link")
        if title_elem is None or link_elem is None:
            return None

        title = clean_text(title_elem.text or "")
        url = (link_elem.text or "").strip()
        if not title or not url:
            return None

        published_at = None
        pub_date_elem = item_elem.find("pubDate")
        if pub_date_elem is not None and pub_date_elem.text:
            try:
                published_at = parsedate_to_datetime(pub_date_elem.text)
                if published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError) as exc:
                logger.warning("Could not parse pub date %r: %s", pub_date_elem.text, exc)

        description_elem = item_elem.find("description")
        summary = clean_text(description_elem.text) if description_elem is not None and description_elem.text else ""

        source_elem = item_elem.find("source")
        source_name = clean_text(source_elem.text) if source_elem is not None and source_elem.text else None
        source_site = source_elem.get("url") if source_elem is not None else None
        publisher_domain = (urlparse(source_site).hostname or "") if source_site else ""

        return NormalizedDocument(
            source_id=uuid.UUID(source_id) if isinstance(source_id, str) else source_id,
            canonical_url=url,
            title=title,
            published_at=published_at,
            retrieved_at=retrieved_at,
            raw_content=f"<item><title>{title}</title><link>{url}</link><description>{summary}</description></item>",
            normalized_content=summary,
            content_type="application/rss+xml",
            metadata={
                "adapter": "google_news",
                "query": query,
                "feed_type": "google_news",
                "source_name": source_name,
                "publisher_domain": publisher_domain,
                "wrapper_url": True,
            },
        )
