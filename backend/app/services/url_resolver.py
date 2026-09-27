"""URL resolution: follow redirects to extract actual publisher URLs from Google News wrappers."""

from __future__ import annotations

import logging
from urllib.parse import urlparse, parse_qs

import httpx

logger = logging.getLogger(__name__)


class UrlResolver:
    """Resolve Google News wrapper URLs to actual publisher URLs."""

    def __init__(self, timeout_seconds: float = 10.0):
        self.timeout_seconds = timeout_seconds

    async def resolve(self, url: str) -> str | None:
        """
        Resolve a URL to its final destination.

        Returns the actual publisher URL, or None if resolution fails.
        Never returns the Google News wrapper URL.
        """
        if not url or not url.startswith("http"):
            return None

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=True,
            ) as client:
                response = await client.head(url, allow_redirects=True)
                final_url = str(response.url)

                # If it's still a Google News URL, try to extract the real URL from query params
                if "news.google.com" in final_url:
                    extracted = self._extract_from_google_news_url(final_url)
                    if extracted and "news.google.com" not in extracted:
                        logger.debug(
                            "Extracted publisher URL from Google News wrapper",
                            extra={"original": url, "extracted": extracted},
                        )
                        return extracted
                    return None

                logger.debug(
                    "Resolved publisher URL",
                    extra={"original": url, "resolved": final_url},
                )
                return final_url

        except httpx.TimeoutException:
            logger.warning(
                "URL resolution timed out",
                extra={"url": url},
            )
            return None
        except (httpx.HTTPError, Exception) as e:
            logger.warning(
                "URL resolution failed",
                extra={"url": url, "error": str(e)},
            )
            return None

    @staticmethod
    def _extract_from_google_news_url(url: str) -> str | None:
        """
        Extract actual URL from Google News parameters if available.

        Google News RSS items sometimes encode the actual URL in query parameters.
        """
        try:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)

            # Try common parameter names where publishers hide real URLs
            for param_name in ["url", "source_url", "article_url", "link"]:
                if param_name in params and params[param_name]:
                    candidate = params[param_name][0]
                    if candidate.startswith("http"):
                        return candidate
        except Exception:
            pass

        return None
