from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .base import SearchProvider, SearchProviderError, SearchResult


class FixtureSearchProvider(SearchProvider):
    name = "fixture"

    def __init__(self, fixture_path: Path) -> None:
        self.fixture_path = fixture_path

    async def search(self, query: str, limit: int, options: object | None = None) -> list[SearchResult]:
        records = self._load_records()
        normalized_query = " ".join(query.lower().split())
        for record in records:
            queries = {" ".join(item.lower().split()) for item in record.get("queries", [])}
            if normalized_query in queries:
                return [self._result(item) for item in record.get("results", [])[:limit]]
        return []

    def _load_records(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SearchProviderError("Search fixture could not be loaded") from exc
        if not isinstance(payload, list):
            raise SearchProviderError("Search fixture must contain a list")
        for record in payload:
            if not isinstance(record, dict):
                raise SearchProviderError("Search fixture contains a malformed query record")
            if not isinstance(record.get("queries"), list) or not isinstance(record.get("results"), list):
                raise SearchProviderError("Search fixture contains a malformed query record")
        return payload

    @staticmethod
    def _result(item: dict[str, Any]) -> SearchResult:
        try:
            return SearchResult.from_values(
                title=str(item["title"]),
                url=str(item["url"]),
                snippet=str(item["snippet"]),
                discovered_at=datetime.fromisoformat(str(item["discovered_at"]).replace("Z", "+00:00")),
                published_at=FixtureSearchProvider._parse_optional_date(item.get("published_at")),
                provider=FixtureSearchProvider.name,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SearchProviderError("Search fixture contains a malformed result") from exc

    @staticmethod
    def _parse_optional_date(value: Any) -> datetime | None:
        if value is None:
            return None
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))