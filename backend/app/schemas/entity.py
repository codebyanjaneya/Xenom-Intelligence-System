from __future__ import annotations

from pydantic import BaseModel


class EntityRegistryOut(BaseModel):
    id: str
    canonical_name: str
    canonical_key: str
    entity_type: str
    status: str