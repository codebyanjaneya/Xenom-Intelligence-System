"""Site-specific and generic HTML/JSON adapters for the source registry.

Every adapter returns ``NormalizedDocument`` objects and records failures in
``self.errors`` (and the log) instead of swallowing them, so ingestion reports
can show exactly what failed.

Access policy: robots.txt is honoured for every crawled host. Hosts that block
automated access (HTTP 403 / robots disallow) are reported, never bypassed.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from app.adapters.ingestion.base import AdapterError, NormalizedDocument, clean_text
from app.services.article_page import FETCH_HEADERS, ArticlePage, fetch_article_page, parse_article_html

logger = logging.getLogger(__name__)

_ROBOTS_CACHE: dict[str, RobotFileParser | None] = {}


async def robots_allowed(url: str, client: httpx.AsyncClient) -> bool:
    """RFC 9309 behaviour: 200 -> parse; 4xx -> allow; 5xx/network -> disallow."""
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    if base not in _ROBOTS_CACHE:
        parser = RobotFileParser()
        try:
            response = await client.get(base + "/robots.txt", timeout=10)
            if response.status_code == 200:
                parser.parse(response.text.splitlines())
                _ROBOTS_CACHE[base] = parser
            elif 400 <= response.status_code < 500:
                _ROBOTS_CACHE[base] = None  # no robots.txt -> everything allowed
            else:
                parser.disallow_all = True
                _ROBOTS_CACHE[base] = parser
        except Exception as exc:
            logger.warning("robots.txt unreachable for %s (%s); treating as disallowed", base, type(exc).__name__)
            parser.disallow_all = True
            _ROBOTS_CACHE[base] = parser
    parser = _ROBOTS_CACHE[base]
    return True if parser is None else parser.can_fetch(FETCH_HEADERS["User-Agent"], url)


async def render_html(url: str, wait_ms: int = 7000) -> str:
    """JS-rendered page HTML via Playwright (single-page apps such as vips.edu, college360)."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise AdapterError("playwright is required to render this source") from exc
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(wait_ms)
            return await page.content()
        finally:
            await browser.close()


def parse_loose_date(value: str | None) -> datetime | None:
    """'31 Aug 2026', 'Tue Jun 13 2023', '8th April 2026', ISO strings -> aware UTC datetime."""
    if not value:
        return None
    iso = re.match(r"\s*(\d{4}-\d{2}-\d{2}(?:[T ][\d:.+Z-]+)?)", value)
    if iso:  # ISO must not go through dayfirst parsing (2026-08-10 would become 8 October)
        try:
            parsed = date_parser.isoparse(iso.group(1).replace(" ", "T"))
        except ValueError:
            return None
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", value.strip(), flags=re.I)
    match = re.search(
        r"(\d{1,2}\s+[A-Za-z]{3,9}\.?,?\s+\d{4}|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2}(?:T[\d:.+Z-]+)?|\d{1,2}[-/]\d{1,2}[-/]\d{4})",
        text,
    )
    if not match:
        return None
    try:
        parsed = date_parser.parse(match.group(1), dayfirst=True)
    except (ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _stable_item_url(page_url: str, text: str) -> str:
    """Unique URL for rows that have no own link (articles.url is unique)."""
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    sep = "&" if "?" in page_url else "?"
    return f"{page_url}{sep}item={digest}"


def document_from_page(
    page: ArticlePage,
    *,
    source_id: uuid.UUID,
    retrieved_at: datetime,
    adapter: str,
    fallback_title: str | None = None,
    extra_meta: dict | None = None,
) -> NormalizedDocument | None:
    title = page.title or fallback_title
    if not title:
        return None
    body = page.body_text or page.description or ""
    canonical = page.canonical_url or page.final_url
    meta = {
        "adapter": adapter,
        "image_candidates": [list(c) for c in page.image_candidates],
        "publisher_domain": urlparse(canonical).hostname or "",
        "author": page.author,
        "date_source": "page" if page.published_at else "none",
        **(extra_meta or {}),
    }
    return NormalizedDocument(
        source_id=source_id,
        canonical_url=canonical,
        title=title,
        published_at=page.published_at,
        retrieved_at=retrieved_at,
        raw_content=body,
        normalized_content=(page.description + " " + body).strip() if page.description and page.description not in body else body,
        content_type="text/html",
        metadata=meta,
    )


class ArticlePageAdapter:
    """One document per given article URL (used for the supplied seed articles)."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    async def fetch(self, urls: list[str], *, source_id: uuid.UUID, retrieved_at: datetime) -> list[NormalizedDocument]:
        docs: list[NormalizedDocument] = []
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=FETCH_HEADERS) as client:
            for url in urls:
                if not await robots_allowed(url, client):
                    self.errors.append(f"robots-disallowed: {url}")
                    continue
                page = await fetch_article_page(url, client=client)
                if page.error:
                    self.errors.append(f"{page.error}: {url}")
                    continue
                doc = document_from_page(page, source_id=source_id, retrieved_at=retrieved_at, adapter="article_page")
                if doc is None:
                    self.errors.append(f"no-title: {url}")
                    continue
                docs.append(doc)
        return docs


class LinkListingAdapter:
    """Listing page -> follow article links -> parse each article page.

    Replaces the previous ``HtmlListingAdapter`` internals, which built
    ``NormalizedDocument`` with non-existent fields and hid every failure behind
    a bare ``except`` (so it always returned 0 documents).
    """

    def __init__(
        self,
        link_pattern: str = r"articleshow|/news/|/article/|/blog/|/press",
        max_links: int = 20,
        concurrency: int = 4,
        render: bool = False,
    ):
        self.render = render
        self.link_re = re.compile(link_pattern, re.I)
        self.max_links = max_links
        self.concurrency = concurrency
        self.errors: list[str] = []

    def extract_links(self, listing_html: str, base_url: str, listing_url: str | None = None) -> dict[str, str]:
        """{article_url: anchor_text} for links matching ``link_pattern`` (pure, no network)."""
        soup = BeautifulSoup(listing_html, "html.parser")
        links: dict[str, str] = {}
        for anchor in soup.find_all("a", href=True):
            href = urljoin(base_url, anchor["href"].strip())
            if not href.startswith("http") or not self.link_re.search(href) or href == listing_url:
                continue
            if href.lower().endswith((".pdf", ".jpg", ".png")):
                continue
            key = href.split("#")[0]
            text = clean_text(anchor.get_text(" "))
            if key not in links or (text and not links[key]):
                links[key] = text
            if len(links) >= self.max_links:
                break
        return links

    async def fetch(self, listing_url: str, *, source_id: uuid.UUID, retrieved_at: datetime) -> list[NormalizedDocument]:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=FETCH_HEADERS) as client:
            if not await robots_allowed(listing_url, client):
                raise AdapterError(f"robots.txt disallows {listing_url}")
            if self.render:
                listing_html, base_url = await render_html(listing_url), listing_url
            else:
                response = await client.get(listing_url)
                if response.status_code != 200:
                    raise AdapterError(f"listing returned HTTP {response.status_code}: {listing_url}")
                listing_html, base_url = response.text, str(response.url)
            links = self.extract_links(listing_html, base_url, listing_url)
            if not links:
                raise AdapterError(f"no article links matched {self.link_re.pattern!r} on {listing_url}")

            sem = asyncio.Semaphore(self.concurrency)
            docs: list[NormalizedDocument] = []

            async def one(url: str, anchor_text: str) -> None:
                async with sem:
                    if not await robots_allowed(url, client):
                        self.errors.append(f"robots-disallowed: {url}")
                        return
                    if self.render:
                        try:
                            page = parse_article_html(await render_html(url, wait_ms=4000), url)
                        except Exception as exc:  # reported, not swallowed
                            self.errors.append(f"render-failed {type(exc).__name__}: {url}")
                            return
                    else:
                        page = await fetch_article_page(url, client=client)
                if page.error:
                    self.errors.append(f"{page.error}: {url}")
                    return
                doc = document_from_page(
                    page, source_id=source_id, retrieved_at=retrieved_at, adapter="link_listing", fallback_title=anchor_text
                )
                if doc:
                    docs.append(doc)
                else:
                    self.errors.append(f"no-title: {url}")

            await asyncio.gather(*(one(u, t) for u, t in links.items()))
        return docs


class TableRowsAdapter:
    """Notice/press-release tables (ISM press releases, VIPS notices/activities).

    One document per row. ``render=True`` uses Playwright for JS-rendered pages
    (vips.edu is a single-page app that serves the same empty shell for every route).
    """

    def __init__(self, render: bool = False, max_rows: int = 40):
        self.render = render
        self.max_rows = max_rows
        self.errors: list[str] = []

    async def _html(self, url: str) -> str:
        if self.render:
            async with httpx.AsyncClient(timeout=10, headers=FETCH_HEADERS) as robots_client:
                if not await robots_allowed(url, robots_client):
                    raise AdapterError(f"robots.txt disallows {url}")
            return await render_html(url)
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=FETCH_HEADERS) as client:
            if not await robots_allowed(url, client):
                raise AdapterError(f"robots.txt disallows {url}")
            response = await client.get(url)
            if response.status_code != 200:
                raise AdapterError(f"HTTP {response.status_code} for {url}")
            return response.text

    @staticmethod
    def _is_date_cell(text: str) -> bool:
        """A cell that IS a date (short), not a title that merely contains one."""
        return len(text) <= 32 and parse_loose_date(text) is not None

    @staticmethod
    def _title_column(headers: list[str]) -> int | None:
        for keyword in ("title", "description", "event name", "event", "subject", "particulars", "name"):
            for idx, header in enumerate(headers):
                if keyword in header:
                    return idx
        return None

    async def fetch(self, url: str, *, source_id: uuid.UUID, retrieved_at: datetime) -> list[NormalizedDocument]:
        return self.parse_html(await self._html(url), url, source_id=source_id, retrieved_at=retrieved_at)

    def parse_html(self, html: str, url: str, *, source_id: uuid.UUID, retrieved_at: datetime) -> list[NormalizedDocument]:
        soup = BeautifulSoup(html, "html.parser")
        rows = soup.select("table tr")
        if not rows:
            raise AdapterError(f"no table rows found on {url}")
        headers = [clean_text(c.get_text(" ")).lower() for c in rows[0].find_all(["th", "td"])]
        docs: list[NormalizedDocument] = []
        for row in rows[1 : self.max_rows + 1]:
            cells = row.find_all(["td", "th"])
            texts = [clean_text(c.get_text(" ")) for c in cells]
            if not any(texts):
                continue
            date_cells = [t for t in texts if self._is_date_cell(t)]
            date = parse_loose_date(date_cells[0]) if date_cells else None
            candidates = [
                t for t in texts
                if t and not re.fullmatch(r"\d+\.?", t) and not self._is_date_cell(t) and t.lower() not in {"view", "download"}
            ]
            if not candidates:
                continue
            title_idx = self._title_column(headers)
            title = texts[title_idx] if title_idx is not None and title_idx < len(texts) and texts[title_idx] else max(candidates[:3], key=len)
            extra = [t for t in candidates if t != title][:3]
            anchor = row.find("a", href=True)
            link = urljoin(url, anchor["href"]) if anchor and not anchor["href"].startswith(("#", "javascript")) else None
            body = " ".join([title] + extra)
            docs.append(
                NormalizedDocument(
                    source_id=source_id,
                    canonical_url=link or _stable_item_url(url, body),
                    title=title,
                    published_at=date,
                    retrieved_at=retrieved_at,
                    raw_content=str(row),
                    normalized_content=body,
                    content_type="text/html",
                    metadata={
                        "adapter": "table_rows",
                        "publisher_domain": urlparse(url).hostname or "",
                        "listing_url": url,
                        "columns": headers,
                        "date_source": "row" if date else "none",
                    },
                )
            )
        if not docs:
            raise AdapterError(f"table on {url} yielded no usable rows")
        return docs


class DefinedgeBlogAdapter:
    """Definedge's blog is a Next.js app that loads posts from its own public JSON API."""

    API = "https://www.definedgesecurities.com/blog/api/posts?action=getLatestPosts&limit={limit}"
    BASE = "https://www.definedgesecurities.com"

    def __init__(self, limit: int = 20):
        self.limit = limit
        self.errors: list[str] = []

    async def fetch(self, *, source_id: uuid.UUID, retrieved_at: datetime) -> list[NormalizedDocument]:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=FETCH_HEADERS) as client:
            if not await robots_allowed(self.API, client):
                raise AdapterError("robots.txt disallows the Definedge blog API")
            response = await client.get(self.API.format(limit=self.limit))
            if response.status_code != 200:
                raise AdapterError(f"Definedge blog API HTTP {response.status_code}")
            try:
                posts = response.json()["blogCommonRes"]
            except (ValueError, KeyError) as exc:
                raise AdapterError(f"Definedge blog API returned unexpected JSON: {exc}") from exc
        return self.parse_posts(posts, source_id=source_id, retrieved_at=retrieved_at)

    def parse_posts(self, posts: list[dict], *, source_id: uuid.UUID, retrieved_at: datetime) -> list[NormalizedDocument]:
        docs: list[NormalizedDocument] = []
        for post in posts:
            slug, title = post.get("slug"), clean_text(post.get("title") or "")
            if not slug or not title:
                self.errors.append(f"post {post.get('postId')} missing slug/title")
                continue
            image = post.get("ogImage") or post.get("featuredImage")
            excerpt = clean_text(post.get("excerpt") or post.get("SEODescription") or "")
            docs.append(
                NormalizedDocument(
                    source_id=source_id,
                    canonical_url=f"{self.BASE}/blog/{slug}",
                    title=title,
                    published_at=parse_loose_date(post.get("publishDate")),
                    retrieved_at=retrieved_at,
                    raw_content=excerpt,
                    normalized_content=excerpt,
                    content_type="application/json",
                    metadata={
                        "adapter": "definedge_blog_api",
                        "publisher_domain": "definedgesecurities.com",
                        "author": post.get("author"),
                        "image_candidates": [["rss_media", urljoin(self.BASE, image)]] if image else [],
                        "date_source": "api",
                    },
                )
            )
        return docs
