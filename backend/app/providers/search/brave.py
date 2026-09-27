from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from .base import SearchProvider, SearchProviderError, SearchResult


class BraveSearchProvider(SearchProvider):
    name = "brave"

    def __init__(self, api_key: str, timeout_seconds: float = 10.0, transport: httpx.AsyncBaseTransport | None = None) -> None:
        if not api_key.strip():
            raise SearchProviderError("Brave Search API key is not configured")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def search(self, query: str, limit: int, options: Any = None) -> list[SearchResult]:
        search_type = getattr(options, "search_type", "web") if options else "web"
        endpoint = "news/search" if search_type == "news" else "web/search"
        params: list[tuple[str, str]] = [("q", query), ("count", str(limit))]
        if options:
            if options.freshness:
                params.append(("freshness", options.freshness))
            for domain in options.domains:
                params.append(("domain", domain))
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self.api_key,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds, transport=self.transport) as client:
                response = await client.get(
                    f"https://api.search.brave.com/res/v1/{endpoint}",
                    params=params,
                    headers=headers,
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SearchProviderError("Brave Search provider request failed") from exc
        return self._parse_results(payload, search_type)

    def _parse_results(self, payload: Any, search_type: str) -> list[SearchResult]:
        if not isinstance(payload, dict):
            raise SearchProviderError("Brave Search returned a malformed response")
        key = "news" if search_type == "news" else "web"
        container = payload.get(key)
        if search_type == "news" and container is None:
            container = payload
        if not isinstance(container, dict) or not isinstance(container.get("results"), list):
            raise SearchProviderError("Brave Search returned a malformed response")
        results: list[SearchResult] = []
        for item in container["results"]:
            if not isinstance(item, dict):
                raise SearchProviderError("Brave Search returned a malformed result")
            results.append(
                SearchResult.from_values(
                    title=str(item.get("title", "")),
                    url=str(item.get("url", "")),
                    snippet=str(item.get("description", "")),
                    discovered_at=datetime.now(timezone.utc),
                    published_at=self._date(item.get("age") or item.get("date")),
                    provider=self.name,
                )
            )
        return results

    @staticmethod
    def _date(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)