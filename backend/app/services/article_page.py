"""Fetch a publisher article page and extract what the pipeline needs.

Server-side only. Image priority (per spec):

    1. RSS/media image        (caller passes ``rss_image``)
    2. structured metadata    (JSON-LD ``image`` / ``thumbnailUrl``)
    3. og:image
    4. twitter:image
    5. no image

Google-hosted images, favicons, logos, sprites and tracking pixels are never
accepted as article imagery, and every candidate is validated over HTTP
(reachable, ``image/*``, not a tiny asset) before it is returned.
"""

from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.adapters.ingestion.base import clean_article_text, clean_text

logger = logging.getLogger(__name__)

# Explicit encodings: some sites (ET, TOI) advertise brotli which httpx cannot always decode here.
FETCH_HEADERS = {
    "User-Agent": "XENOM-Intel/0.1 (public-page fetch)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Encoding": "gzip, deflate",
}

_BAD_IMAGE_HOSTS = ("news.google.com", "gstatic.com", "googleusercontent.com", "google.com", "googletagmanager.com")
_BAD_IMAGE_HINTS = re.compile(
    r"(favicon|/logo|logo[-_.]|[-_.]logo|sprite|placeholder|default[-_]image|blank\.|pixel|1x1|spacer|tracking|/icons?/|"
    r"apple-touch|avatar|gravatar|share[-_]?image[-_]?default|noimage|no-image)",
    re.I,
)
MIN_IMAGE_BYTES = 4_000
MIN_IMAGE_SIDE = 120


@dataclass
class ArticlePage:
    url: str
    final_url: str
    status: int
    title: str | None = None
    canonical_url: str | None = None
    published_at: datetime | None = None
    author: str | None = None
    description: str | None = None
    body_text: str = ""
    image_candidates: list[tuple[str, str]] = field(default_factory=list)  # (source, url)
    error: str | None = None


def is_acceptable_image_url(url: str | None) -> bool:
    if not url or not url.startswith(("http://", "https://")):
        return False
    host = (urlparse(url).hostname or "").lower()
    if any(host == bad or host.endswith("." + bad) for bad in _BAD_IMAGE_HOSTS):
        return False
    path = urlparse(url).path
    if path.lower().endswith((".svg", ".ico", ".gif")):
        return False
    # host is checked too: e.g. s3-symbol-logo.tradingview.com serves company logos, not article art
    return not _BAD_IMAGE_HINTS.search(host + path)


def _parse_dt(value: str | None) -> datetime | None:
    """ISO-8601 (incl. HTML-escaped '+' and single-digit hours like 'T7:25:22'), RFC 2822, or loose dates."""
    if not value or not isinstance(value, str):
        return None
    value = html.unescape(value).strip()
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            from email.utils import parsedate_to_datetime

            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            try:
                from dateutil import parser as date_parser

                parsed = date_parser.parse(value)
            except (ValueError, OverflowError):
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _first_date(*candidates: str | None) -> datetime | None:
    """First candidate that actually parses (a malformed JSON-LD date must not hide a good meta date)."""
    for candidate in candidates:
        parsed = _parse_dt(candidate)
        if parsed is not None:
            return parsed
    return None


def _meta(soup: BeautifulSoup, **attrs: str) -> str | None:
    tag = soup.find("meta", attrs=attrs)
    content = tag.get("content") if tag else None
    return content.strip() if content and content.strip() else None


def _ld_nodes(soup: BeautifulSoup) -> list[dict]:
    nodes: list[dict] = []
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text() or ""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                nodes.append(item)
                graph = item.get("@graph")
                if isinstance(graph, list):
                    stack.extend(graph)
            elif isinstance(item, list):
                stack.extend(item)
    return nodes


def _ld_images(node: dict) -> list[str]:
    urls: list[str] = []
    for key in ("image", "thumbnailUrl"):
        value = node.get(key)
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, str):
                urls.append(item)
            elif isinstance(item, dict):
                candidate = item.get("url") or item.get("contentUrl")
                if isinstance(candidate, str):
                    urls.append(candidate)
    return urls


_ARTICLE_TYPES = {"newsarticle", "article", "blogposting", "reportage", "analysisnewsarticle", "webpage"}


def parse_article_html(html: str, url: str, final_url: str | None = None, status: int = 200) -> ArticlePage:
    final_url = final_url or url
    soup = BeautifulSoup(html, "html.parser")
    page = ArticlePage(url=url, final_url=final_url, status=status)

    nodes = _ld_nodes(soup)
    article_node = next(
        (n for n in nodes if str(n.get("@type", "")).lower().strip("[]'\" ") in _ARTICLE_TYPES and n.get("headline")),
        None,
    ) or next((n for n in nodes if str(n.get("@type", "")).lower() in _ARTICLE_TYPES), None)

    page.title = clean_text(
        (article_node or {}).get("headline") or _meta(soup, property="og:title") or (soup.title.string if soup.title else "") or ""
    ) or None
    page.description = clean_text(_meta(soup, property="og:description") or _meta(soup, name="description") or "") or None

    canonical = None
    link = soup.find("link", rel="canonical")
    if link and link.get("href"):
        canonical = urljoin(final_url, link["href"].strip())
    canonical = canonical or _meta(soup, property="og:url")
    if canonical:
        page.canonical_url = urljoin(final_url, canonical)

    page.published_at = _first_date(
        (article_node or {}).get("datePublished"),
        *[n.get("datePublished") for n in nodes if n is not article_node],
        _meta(soup, property="article:published_time"),
        _meta(soup, itemprop="datePublished"),
        _meta(soup, name="publishdate"),
        _meta(soup, name="date"),
    )
    if page.published_at is None:
        time_tag = soup.find("time", attrs={"datetime": True})
        if time_tag:
            page.published_at = _parse_dt(time_tag["datetime"])

    author = (article_node or {}).get("author")
    if isinstance(author, list) and author:
        author = author[0]
    if isinstance(author, dict):
        author = author.get("name")
    page.author = clean_text(author) if isinstance(author, str) and author.strip() else _meta(soup, name="author")

    # image candidates in spec priority order
    for node in ([article_node] if article_node else []) + [n for n in nodes if n is not article_node]:
        for img in _ld_images(node):
            page.image_candidates.append(("jsonld", urljoin(final_url, img)))
    og = _meta(soup, property="og:image") or _meta(soup, property="og:image:url")
    if og:
        page.image_candidates.append(("og:image", urljoin(final_url, og)))
    tw = _meta(soup, name="twitter:image") or _meta(soup, name="twitter:image:src") or _meta(soup, property="twitter:image")
    if tw:
        page.image_candidates.append(("twitter:image", urljoin(final_url, tw)))

    container = soup.find("article") or soup.find("main") or soup.body or soup
    # Last-resort image candidate: the first real content image inside the article body itself, for pages with
    # no jsonld/og/twitter image metadata. Same acceptability filter (no logos/icons/tracking pixels) and the
    # same live validation (pick_valid_image) as every other candidate, so nothing unverified is ever used.
    for img in container.find_all("img", limit=8):
        src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
        if src and not src.startswith("data:"):
            candidate = urljoin(final_url, src.strip())
            if is_acceptable_image_url(candidate):
                page.image_candidates.append(("article_body", candidate))
                break
    for tag in container(["script", "style", "nav", "footer", "header", "aside", "form", "noscript", "figure"]):
        tag.decompose()
    paragraphs = [p.get_text(" ", strip=True) for p in container.find_all("p")]
    page.body_text = clean_article_text(paragraphs)
    return page


async def fetch_article_page(url: str, timeout: float = 12.0, client: httpx.AsyncClient | None = None) -> ArticlePage:
    own = client is None
    client = client or httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=FETCH_HEADERS)
    try:
        response = await client.get(url)
        if response.status_code != 200:
            return ArticlePage(url=url, final_url=str(response.url), status=response.status_code,
                               error=f"HTTP {response.status_code}")
        if "html" not in (response.headers.get("content-type") or ""):
            return ArticlePage(url=url, final_url=str(response.url), status=response.status_code,
                               error=f"non-html content-type {response.headers.get('content-type')}")
        return parse_article_html(response.text, url, str(response.url), response.status_code)
    except httpx.TimeoutException:
        return ArticlePage(url=url, final_url=url, status=0, error="timeout")
    except Exception as exc:  # network/decode errors are reported, never swallowed silently
        return ArticlePage(url=url, final_url=url, status=0, error=f"{type(exc).__name__}: {str(exc)[:120]}")
    finally:
        if own:
            await client.aclose()


def _image_size(data: bytes) -> tuple[int, int] | None:
    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(data)) as img:
            return img.size
    except Exception:
        return None


async def validate_image(url: str, client: httpx.AsyncClient) -> tuple[bool, str]:
    """Reachable, image/*, big enough. Returns (ok, reason)."""
    if not is_acceptable_image_url(url):
        return False, "rejected-by-url-rules"
    try:
        response = await client.get(url, headers={**FETCH_HEADERS, "Accept": "image/*"})
    except Exception as exc:
        return False, f"unreachable:{type(exc).__name__}"
    if response.status_code != 200:
        return False, f"http-{response.status_code}"
    ctype = (response.headers.get("content-type") or "").lower()
    if not ctype.startswith("image/") or "svg" in ctype or "icon" in ctype:
        return False, f"content-type:{ctype or 'none'}"
    if len(response.content) < MIN_IMAGE_BYTES:
        return False, f"too-small:{len(response.content)}B"
    size = _image_size(response.content)
    if size and min(size) < MIN_IMAGE_SIDE:
        return False, f"dimensions:{size[0]}x{size[1]}"
    return True, "ok"


async def pick_valid_image(
    candidates: list[tuple[str, str]], client: httpx.AsyncClient
) -> tuple[str | None, str | None, list[str]]:
    """First candidate (in priority order) that validates. Returns (url, source, rejection log)."""
    log: list[str] = []
    seen: set[str] = set()
    for source, url in candidates:
        if url in seen:
            continue
        seen.add(url)
        ok, reason = await validate_image(url, client)
        if ok:
            return url, source, log
        log.append(f"{source}:{reason}")
    return None, None, log
