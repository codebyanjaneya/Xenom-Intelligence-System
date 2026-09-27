from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from app.providers.search import SearchResult
from app.services.normalization import normalize_url


@dataclass(frozen=True)
class QualityDecision:
    result: SearchResult
    accepted: bool
    reason: str


def quality_gate(results: list[SearchResult]) -> list[QualityDecision]:
    seen_urls: set[str] = set()
    decisions: list[QualityDecision] = []
    for result in results:
        parsed = urlsplit(result.url)
        normalized = normalize_url(result.url) if parsed.scheme and parsed.netloc else ""
        if not parsed.scheme in {"http", "https"} or not parsed.netloc:
            decisions.append(QualityDecision(result, False, "rejected_invalid_url"))
        elif not result.title.strip():
            decisions.append(QualityDecision(result, False, "rejected_missing_title"))
        elif not result.snippet.strip():
            decisions.append(QualityDecision(result, False, "rejected_missing_content"))
        elif normalized in seen_urls:
            decisions.append(QualityDecision(result, False, "rejected_duplicate_url"))
        else:
            seen_urls.add(normalized)
            decisions.append(QualityDecision(result, True, "accepted"))
    return decisions