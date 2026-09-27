"""Tests for canonical URL resolution."""

import asyncio
import httpx
from unittest.mock import AsyncMock, patch

from app.services.url_resolver import UrlResolver


class TestUrlResolver:
    """Test URL resolution for publisher URLs."""

    def setup_method(self):
        self.resolver = UrlResolver(timeout_seconds=5.0)

    def test_resolve_direct_publisher_url(self):
        """Direct publisher URLs should be returned as-is."""
        publisher_url = "https://www.npr.org/article/example"

        async def run_test():
            with patch("app.services.url_resolver.httpx.AsyncClient") as mock_client_class:
                mock_response = AsyncMock()
                mock_response.url = publisher_url
                mock_client = AsyncMock()
                mock_client.head = AsyncMock(return_value=mock_response)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client

                result = await self.resolver.resolve(publisher_url)
                assert result == publisher_url

        asyncio.run(run_test())

    def test_resolve_google_news_wrapper_to_publisher(self):
        """Google News wrapper URLs should resolve to actual publisher."""
        google_news_url = "https://news.google.com/rss/articles/CBMi..."
        publisher_url = "https://www.npr.org/article/example"

        async def run_test():
            with patch("app.services.url_resolver.httpx.AsyncClient") as mock_client_class:
                mock_response = AsyncMock()
                mock_response.url = publisher_url
                mock_client = AsyncMock()
                mock_client.head = AsyncMock(return_value=mock_response)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client

                result = await self.resolver.resolve(google_news_url)
                assert result == publisher_url
                assert "news.google.com" not in result

        asyncio.run(run_test())

    def test_resolve_returns_none_on_timeout(self):
        """Timeout should return None, not raise."""
        google_news_url = "https://news.google.com/rss/articles/CBMi..."

        async def run_test():
            with patch("app.services.url_resolver.httpx.AsyncClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.head = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client

                result = await self.resolver.resolve(google_news_url)
                assert result is None

        asyncio.run(run_test())

    def test_resolve_returns_none_on_http_error(self):
        """HTTP errors should return None."""
        google_news_url = "https://news.google.com/rss/articles/CBMi..."

        async def run_test():
            with patch("app.services.url_resolver.httpx.AsyncClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.head = AsyncMock(side_effect=httpx.HTTPError("404"))
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client

                result = await self.resolver.resolve(google_news_url)
                assert result is None

        asyncio.run(run_test())

    def test_resolve_empty_url_returns_none(self):
        """Empty URLs should return None."""
        async def run_test():
            result = await self.resolver.resolve("")
            assert result is None

        asyncio.run(run_test())

    def test_resolve_non_http_url_returns_none(self):
        """Non-HTTP URLs should return None."""
        async def run_test():
            result = await self.resolver.resolve("not-a-url")
            assert result is None

        asyncio.run(run_test())

    def test_extract_from_google_news_url_no_params(self):
        """URL without publisher params should return None."""
        url = "https://news.google.com/rss/articles/CBMi..."
        result = UrlResolver._extract_from_google_news_url(url)
        assert result is None

    def test_extract_from_google_news_url_with_url_param(self):
        """URL parameter should be extracted."""
        publisher_url = "https://www.npr.org/article"
        url = f"https://news.google.com/rss/articles?url={publisher_url}&other=value"
        result = UrlResolver._extract_from_google_news_url(url)
        assert result == publisher_url

    def test_never_return_google_news_as_canonical(self):
        """If final URL is still Google News, should return None."""
        google_news_url = "https://news.google.com/rss/articles/CBMi..."

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_response = AsyncMock()
            mock_response.url = google_news_url  # Still Google News
            mock_client = AsyncMock()
            mock_client.head = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            # Should return None since it's still a Google News URL
            import asyncio
            result = asyncio.run(self.resolver.resolve(google_news_url))
            assert result is None
