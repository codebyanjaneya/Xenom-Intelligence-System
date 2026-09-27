from __future__ import annotations

from .base import SearchProvider, SearchProviderError, SearchResult


class UnconfiguredSearchProvider(SearchProvider):
    name = "unconfigured"

    async def search(self, query: str, limit: int, options: object | None = None) -> list[SearchResult]:
        raise SearchProviderError("No live search provider is configured")