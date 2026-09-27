from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit


class SearchProviderError(RuntimeError):
    """Raised when a search provider cannot return normalized results."""


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    domain: str
    discovered_at: datetime
    published_at: datetime | None = None
    provider: str = "unknown"
    result_id: uuid.UUID | None = None

    @classmethod
    def from_values(
        cls,
        *,
        title: str,
        url: str,
        snippet: str,
        discovered_at: datetime | None = None,
        published_at: datetime | None = None,
        provider: str = "unknown",
        result_id: uuid.UUID | None = None,
    ) -> "SearchResult":
        parsed = urlsplit(url)
        return cls(
            title=" ".join(title.split()),
            url=url.strip(),
            snippet=" ".join(snippet.split()),
            domain=(parsed.hostname or "").lower(),
            discovered_at=discovered_at or datetime.now(timezone.utc),
            published_at=published_at,
            provider=provider,
            result_id=result_id,
        )


class SearchProvider(ABC):
    name: str

    @abstractmethod
    async def search(self, query: str, limit: int, options: object | None = None) -> list[SearchResult]:
        raise NotImplementedError