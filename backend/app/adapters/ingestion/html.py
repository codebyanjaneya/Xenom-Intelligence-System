from __future__ import annotations

import uuid
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

from .base import AdapterError, IngestionAdapter, NormalizedDocument, clean_text, retrieval_time


class _ArticleParser(HTMLParser):
    _ignored = {"script", "style", "nav", "header", "footer", "aside", "form"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.heading_parts: list[str] = []
        self.body_parts: list[str] = []
        self.date_values: list[str] = []
        self._stack: list[str] = []
        self._ignored_depth = 0
        self._article_depth = 0
        self._title_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = dict(attrs)
        self._stack.append(tag)
        if tag in self._ignored:
            self._ignored_depth += 1
        if tag in {"article", "main"}:
            self._article_depth += 1
        if tag == "title":
            self._title_depth += 1
        if tag == "time":
            value = attributes.get("datetime")
            if value:
                self.date_values.append(value)
        if tag == "meta":
            key = (attributes.get("property") or attributes.get("name") or "").lower()
            if key in {"article:published_time", "date", "pubdate", "published"}:
                value = attributes.get("content")
                if value:
                    self.date_values.append(value)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._ignored and self._ignored_depth:
            self._ignored_depth -= 1
        if tag in {"article", "main"} and self._article_depth:
            self._article_depth -= 1
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
        if self._stack:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        if self._ignored_depth or not data.strip():
            return
        text = " ".join(data.split())
        if self._title_depth:
            self.title_parts.append(text)
        if self._stack and self._stack[-1] in {"h1", "h2"}:
            self.heading_parts.append(text)
        if self._article_depth or (self._stack and self._stack[-1] == "body"):
            self.body_parts.append(text)


class HtmlFixtureAdapter(IngestionAdapter):
    content_type = "text/html"

    def parse(
        self,
        raw_content: str,
        *,
        source_id: uuid.UUID,
        source_url: str,
        retrieved_at: datetime | None = None,
    ) -> list[NormalizedDocument]:
        parser = _ArticleParser()
        try:
            parser.feed(raw_content)
            parser.close()
        except Exception as exc:
            raise AdapterError("HTML fixture could not be parsed") from exc

        title = clean_text(" ".join(parser.title_parts or parser.heading_parts)) or None
        text = clean_text(" ".join(parser.body_parts))
        return [
            NormalizedDocument(
                source_id=source_id,
                canonical_url=source_url,
                title=title,
                published_at=self._parse_date(parser.date_values),
                retrieved_at=retrieval_time(retrieved_at),
                raw_content=raw_content,
                normalized_content=text,
                content_type=self.content_type,
                metadata={"adapter": "html_fixture"},
            )
        ]

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
            raise AdapterError("HTML fixture could not be read") from exc
        return self.parse(
            raw_content,
            source_id=source_id,
            source_url=source_url,
            retrieved_at=retrieved_at,
        )

    @staticmethod
    def _parse_date(values: list[str]) -> datetime | None:
        for value in values:
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.astimezone()
            return parsed
        return None
