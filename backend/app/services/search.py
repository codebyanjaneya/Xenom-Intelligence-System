from __future__ import annotations

from pathlib import Path

from app.config.settings import get_settings
from app.providers.search import (
    BraveSearchProvider,
    FixtureSearchProvider,
    SearchProvider,
    SearchProviderError,
    SearchResult,
    UnconfiguredSearchProvider,
)
from app.schemas.search import SearchRequest, SearchResponse, SearchResultOut
from app.services.search_quality import quality_gate
from app.services.search_scoring import score_result

_FIXTURE_PATH = Path(__file__).resolve().parents[1] / "providers" / "search" / "fixtures" / "m6-search-results.json"


def get_search_provider() -> SearchProvider:
    mode = get_settings().search_provider.lower()
    if mode == "fixture":
        if get_settings().environment.strip().lower() == "production":
            raise SearchProviderError("Fixture search provider is disabled in production")
        return FixtureSearchProvider(_FIXTURE_PATH)
    if mode == "brave":
        settings = get_settings()
        if not settings.brave_api_key:
            raise SearchProviderError("Brave Search API key is not configured")
        return BraveSearchProvider(settings.brave_api_key)
    return UnconfiguredSearchProvider()


class SearchService:
    def __init__(self, provider: SearchProvider | None = None) -> None:
        self.provider = provider or get_search_provider()

    async def search(self, request: SearchRequest) -> SearchResponse:
        query = " ".join(request.query.split())
        if not query:
            raise ValueError("query must not be blank")
        provider_results = await self.provider.search(query, request.limit, request)
        decisions = quality_gate(provider_results)
        accepted = [decision.result for decision in decisions if decision.accepted]
        scored = [(result, score_result(query, result)) for result in accepted]
        scored.sort(
            key=lambda pair: (
                -pair[1].score,
                -(pair[0].published_at.timestamp() if pair[0].published_at else 0),
                pair[0].url,
            )
        )
        results = [self._normalize_result(result, breakdown) for result, breakdown in scored[: request.limit]]
        rejected: dict[str, int] = {}
        for decision in decisions:
            if not decision.accepted:
                rejected[decision.reason] = rejected.get(decision.reason, 0) + 1
        return SearchResponse(
            query=query,
            provider=self.provider.name,
            results=results,
            count=len(results),
            rejected=rejected,
        )

    @staticmethod
    def _normalize_result(result: SearchResult, breakdown) -> SearchResultOut:
        return SearchResultOut(
            title=" ".join(result.title.split()),
            url=result.url.strip(),
            snippet=" ".join(result.snippet.split()),
            domain=result.domain.lower(),
            discovered_at=result.discovered_at,
            published_at=result.published_at,
            provider=result.provider,
            relevance_score=round(breakdown.score, 2),
            score_breakdown={
                "entity_match": round(breakdown.entity_match, 2),
                "topic_match": round(breakdown.topic_match, 2),
                "recency": round(breakdown.recency, 2),
                "source_quality": round(breakdown.source_quality, 2),
                "content_signal": round(breakdown.content_signal, 2),
            },
        )


__all__ = ["SearchProviderError", "SearchService"]