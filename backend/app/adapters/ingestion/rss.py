from __future__ import annotations

import uuid
import asyncio
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree

import httpx
from dataclasses import replace

from .base import AdapterError, IngestionAdapter, NormalizedDocument, clean_text, retrieval_time


class RssFixtureAdapter(IngestionAdapter):
    content_type = "application/rss+xml"

    def parse(
        self,
        raw_content: str,
        *,
        source_id: uuid.UUID,
        source_url: str,
        retrieved_at: datetime | None = None,
    ) -> list[NormalizedDocument]:
        try:
            root = ElementTree.fromstring(raw_content)
        except ElementTree.ParseError as exc:
            raise AdapterError("RSS fixture is not valid XML") from exc

        documents: list[NormalizedDocument] = []
        for index, item in enumerate(self._items(root)):
            title = self._child_text(item, "title")
            url = self._link(item) or source_url
            summary = self._first_text(item, ("encoded", "description", "summary", "content"))
            published_at = self._parse_date(
                self._first_text(item, ("pubDate", "published", "updated", "date"))
            )
            image_url = self._extract_image(item)
            raw_item = ElementTree.tostring(item, encoding="unicode")
            metadata = {"adapter": "rss_fixture", "entry_index": index}
            if image_url:
                metadata["image_url"] = image_url
            documents.append(
                NormalizedDocument(
                    source_id=source_id,
                    canonical_url=url,
                    title=title or None,
                    published_at=published_at,
                    retrieved_at=retrieval_time(retrieved_at),
                    raw_content=raw_item,
                    normalized_content=clean_text(summary or title or ""),
                    content_type=self.content_type,
                    metadata=metadata,
                )
            )
        return documents

    def parse_file(
        self,
        path: Path,
        *,
        source_id: uuid.UUID,
        source_url: str,
        retrieved_at: datetime | None = None,
    ) -> list[NormalizedDocument]:
        try:
            raw_content = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise AdapterError("RSS fixture could not be read") from exc
        return self.parse(
            raw_content,
            source_id=source_id,
            source_url=source_url,
            retrieved_at=retrieved_at,
        )

    @staticmethod
    def _items(root: ElementTree.Element) -> list[ElementTree.Element]:
        return [element for element in root.iter() if RssFixtureAdapter._local_name(element.tag) in {"item", "entry"}]

    @staticmethod
    def _local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    @classmethod
    def _child_text(cls, element: ElementTree.Element, name: str) -> str | None:
        for child in element:
            if cls._local_name(child.tag).lower() == name.lower():
                text = "".join(child.itertext()).strip()
                if text:
                    return text
        return None

    @classmethod
    def _first_text(cls, element: ElementTree.Element, names: tuple[str, ...]) -> str | None:
        for name in names:
            value = cls._child_text(element, name)
            if value:
                return value
        return None

    @classmethod
    def _link(cls, element: ElementTree.Element) -> str | None:
        for child in element:
            if cls._local_name(child.tag).lower() != "link":
                continue
            href = child.attrib.get("href")
            value = href or "".join(child.itertext()).strip()
            if value:
                return value
        return None

    @classmethod
    def _extract_image(cls, element: ElementTree.Element) -> str | None:
        """Extract image URL from RSS item (og:image, media:content, media:thumbnail, etc.)."""
        # Try media:content first (common in RSS)
        for child in element:
            tag_name = cls._local_name(child.tag).lower()
            if tag_name == "content" and child.attrib.get("medium") == "image":
                url = child.attrib.get("url")
                if url:
                    return url

        # Try media:thumbnail
        for child in element:
            tag_name = cls._local_name(child.tag).lower()
            if tag_name == "thumbnail":
                url = child.attrib.get("url")
                if url:
                    return url

        # Try image element
        for child in element:
            tag_name = cls._local_name(child.tag).lower()
            if tag_name == "image":
                url = child.attrib.get("url") or "".join(child.itertext()).strip()
                if url:
                    return url

        # Try enclosure with image type
        for child in element:
            tag_name = cls._local_name(child.tag).lower()
            if tag_name == "enclosure":
                content_type = child.attrib.get("type", "")
                if "image" in content_type:
                    url = child.attrib.get("url")
                    if url:
                        return url

        return None

    @staticmethod
    def _parse_date(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        return parsed


class RssHttpAdapter(RssFixtureAdapter):
    """Fetch a public RSS/Atom feed, then reuse the existing XML parser."""

    def __init__(
        self,
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def fetch(
        self,
        feed_url: str,
        *,
        source_id: uuid.UUID,
        retrieved_at: datetime | None = None,
    ) -> list[NormalizedDocument]:
        response = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True, transport=self.transport) as client:
                    response = await client.get(feed_url, headers={
                        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml",
                        "Accept-Encoding": "identity",
                    })
                    if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                        await asyncio.sleep(min(2 ** attempt, 8))
                        continue
                    response.raise_for_status()
                break
            except httpx.TimeoutException as exc:
                if attempt == 2:
                    raise AdapterError("RSS feed request timed out") from exc
                await asyncio.sleep(2 ** attempt)
            except httpx.HTTPStatusError as exc:
                if attempt == 2:
                    raise AdapterError(f"RSS feed returned HTTP {exc.response.status_code}") from exc
                await asyncio.sleep(2 ** attempt)
            except httpx.RequestError as exc:
                if attempt == 2:
                    raise AdapterError("RSS feed request failed") from exc
                await asyncio.sleep(2 ** attempt)

        try:
            raw_content = response.text
        except UnicodeDecodeError as exc:
            raise AdapterError("RSS feed response was not valid text") from exc

        documents = self.parse(
            raw_content,
            source_id=source_id,
            source_url=feed_url,
            retrieved_at=retrieved_at,
        )
        return [
            replace(document, metadata={**document.metadata, "adapter": "rss_http"})
            for document in documents
        ]
