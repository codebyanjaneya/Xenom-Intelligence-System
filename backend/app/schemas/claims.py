from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.intelligence import Evidence


ClaimStatus = Literal["unreviewed", "verified", "rejected"]


class ClaimCreate(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    claim_text: str = Field(min_length=1, max_length=2000)
    entity_key: str | None = None
    event_key: str | None = None
    evidence: list[Evidence] = Field(min_length=1)

    @model_validator(mode="after")
    def reference_required(self) -> "ClaimCreate":
        if not self.entity_key and not self.event_key:
            raise ValueError("claim must reference an entity and/or event")
        return self


class ClaimOut(BaseModel):
    claim_id: UUID
    claim_text: str
    entity_key: str | None
    event_key: str | None
    evidence: list[Evidence]
    status: ClaimStatus
    provenance_status: Literal["VERIFIED_IN_STORED_CORPUS", "UNRESOLVED", "UNCHECKED"] = "UNCHECKED"


class ClaimStatusUpdate(BaseModel):
    status: ClaimStatus