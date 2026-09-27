from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, computed_field

from app.services.freshness import get_freshness_label


class ArticleOut(BaseModel):
    id: uuid.UUID
    title: str | None
    url: str
    canonical_url: str | None = None
    image_url: str | None = None
    source_name: str | None
    published_at: datetime | None
    fetched_at: datetime | None
    summary: str | None
    source_id: uuid.UUID | None
    entity_names: list[str] = []  # Clients/entities this article is associated with
    topic: str | None = None  # Industry/topic classification
    quality_score: float | None = None
    relevance: dict | None = None  # why this article was accepted (entity, matched terms/topics, scores)

    @computed_field
    @property
    def freshness_label(self) -> str | None:
        """Human-readable freshness label based on publication date."""
        return get_freshness_label(self.published_at)
