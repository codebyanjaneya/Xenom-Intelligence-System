from __future__ import annotations

import asyncio

import pytest
import httpx

from app.providers.search import BraveSearchProvider, SearchProviderError
from app.services.search import get_search_provider


def test_missing_brave_key_fails_safely(monkeypatch) -> None:
    monkeypatch.setenv("SEARCH_PROVIDER", "brave")
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    from app.config.settings import get_settings

    get_settings.cache_clear()
    try:
        with pytest.raises(SearchProviderError, match="API key is not configured"):
            get_search_provider()
    finally:
        get_settings.cache_clear()


def test_unsupported_provider_does_not_fallback_to_live(monkeypatch) -> None:
    monkeypatch.setenv("SEARCH_PROVIDER", "unknown-provider")
    from app.config.settings import get_settings

    get_settings.cache_clear()
    try:
        provider = get_search_provider()
        assert provider.name == "unconfigured"
    finally:
        get_settings.cache_clear()


def test_brave_malformed_response_fails_safely() -> None:
    provider = BraveSearchProvider("test-key")

    with pytest.raises(SearchProviderError, match="malformed response"):
        provider._parse_results({"unexpected": []}, "web")


def test_brave_normalizes_web_result_without_network() -> None:
    provider = BraveSearchProvider("test-key")

    results = provider._parse_results(
        {"web": {"results": [{"title": "Title", "url": "https://example.test/a", "description": "Snippet"}]}},
        "web",
    )

    assert len(results) == 1
    assert results[0].provider == "brave"
    assert results[0].domain == "example.test"


def test_brave_http_failure_is_wrapped() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    provider = BraveSearchProvider("test-key", transport=httpx.MockTransport(handler))

    async def run() -> None:
        with pytest.raises(SearchProviderError, match="request failed"):
            await provider.search("AI robotics", 3)

    asyncio.run(run())