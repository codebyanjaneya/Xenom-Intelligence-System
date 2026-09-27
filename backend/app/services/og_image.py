"""Publisher image lookup (thin wrapper over ``article_page``).

Image priority: RSS/media (caller) -> JSON-LD -> og:image -> twitter:image -> none.
Google-hosted images, logos, favicons and tracking pixels are never returned.
"""

from __future__ import annotations

import logging

import httpx

from app.services.article_page import FETCH_HEADERS, fetch_article_page, pick_valid_image
from app.services.google_news_decoder import is_google_news_url, resolve_google_news_url

logger = logging.getLogger(__name__)


async def extract_og_image(url: str, timeout: int = 8) -> str | None:
    """Real publisher article image for ``url`` (Google wrapper links are resolved first), or None."""
    if not url:
        return None
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=FETCH_HEADERS) as client:
        target = await resolve_google_news_url(url, client) if is_google_news_url(url) else url
        if not target:
            return None
        page = await fetch_article_page(target, client=client)
        if page.error:
            logger.debug("image lookup skipped for %s: %s", target[:80], page.error)
            return None
        image, _source, _rejected = await pick_valid_image(page.image_candidates, client)
        return image
