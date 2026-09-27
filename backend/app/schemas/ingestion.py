from __future__ import annotations

import uuid

from pydantic import BaseModel


class IngestionResult(BaseModel):
    source_id: uuid.UUID
    adapter: str
    documents_found: int
    articles_created: int
    articles_deduplicated: int = 0