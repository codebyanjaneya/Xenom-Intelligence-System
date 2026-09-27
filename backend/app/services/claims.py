from __future__ import annotations

import hashlib
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Claim
from app.schemas.claims import ClaimCreate, ClaimOut, ClaimStatusUpdate
from app.schemas.intelligence import IntelligenceRequest
from app.services.stored_intelligence import extract_from_stored
from app.services.trace_audit import audit_evidence_items


class ClaimValidationError(ValueError):
    """Raised when a claim reference or evidence is not supported."""


_CLAIMS: dict[UUID, ClaimOut] = {}


class StoredIntelligenceSource:
    """Claim validation source: the DB-backed search -> evidence -> entity/event path (no fixtures)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def extract(self, request: IntelligenceRequest):
        return await extract_from_stored(self.session, request)


class ClaimService:
    """`intelligence` is anything with `async extract(IntelligenceRequest)`; by default the stored-article path
    over `session`. Without a session or an injected source there is nothing to validate against, so creation fails."""

    def __init__(self, intelligence=None, session: AsyncSession | None = None) -> None:
        self.intelligence = intelligence or (StoredIntelligenceSource(session) if session is not None else None)
        self.session = session

    async def create(self, payload: ClaimCreate) -> ClaimOut:
        if self.intelligence is None:
            raise ClaimValidationError("no intelligence source available to validate the claim")
        try:
            intelligence = await self.intelligence.extract(IntelligenceRequest(query=" ".join(payload.query.split())))
        except ValueError as exc:
            raise ClaimValidationError(str(exc)) from exc
        entity_keys = {entity.canonical_key for entity in intelligence.entities}
        event_keys = {event.event_key for event in intelligence.events}
        if payload.entity_key and payload.entity_key not in entity_keys:
            raise ClaimValidationError("claim references an unknown entity")
        if payload.event_key and payload.event_key not in event_keys:
            raise ClaimValidationError("claim references an unknown event")
        supported = {
            (item.source_url, item.content_span, item.page_title)
            for owner in [*intelligence.entities, *intelligence.events]
            for item in owner.evidence
        }
        if any((item.source_url, item.content_span, item.page_title) not in supported for item in payload.evidence):
            raise ClaimValidationError("claim evidence is not supported by extracted intelligence")
        claim_key = hashlib.sha256("|".join([
            payload.claim_text.strip(), payload.entity_key or "", payload.event_key or "",
            *sorted(f"{item.source_url}|{item.content_span}" for item in payload.evidence),
        ]).encode("utf-8")).hexdigest()
        evidence_json = [item.model_dump(mode="json") for item in payload.evidence]
        if self.session is None:
            claim_id = UUID(bytes=bytes.fromhex(claim_key[:32]))
            claim = _CLAIMS.get(claim_id)
            if claim is None:
                claim = ClaimOut(claim_id=claim_id, claim_text=" ".join(payload.claim_text.split()), entity_key=payload.entity_key, event_key=payload.event_key, evidence=payload.evidence, status="unreviewed")
                _CLAIMS[claim_id] = claim
            return claim
        existing = await self.session.scalar(select(Claim).where(Claim.claim_key == claim_key))
        if existing is None:
            existing = Claim(claim_text=" ".join(payload.claim_text.split()), claim_key=claim_key, entity_key=payload.entity_key, event_key=payload.event_key, evidence_json=evidence_json, verification_status="unreviewed")
            self.session.add(existing)
            await self.session.commit()
            await self.session.refresh(existing)
        return self._out(existing)

    async def list(self) -> list[ClaimOut]:
        """Claims newest first. Each carries whether its evidence still resolves to a stored article and verbatim text;
        historical (fixture-era) claims are listed but flagged UNRESOLVED, never trusted."""
        if self.session is None:
            return list(_CLAIMS.values())
        rows = await self.session.scalars(select(Claim).order_by(Claim.created_at.desc()))
        out = []
        for row in rows:
            claim = self._out(row)
            report = await audit_evidence_items(self.session, "claim", claim.evidence)
            out.append(claim.model_copy(update={
                "provenance_status": "UNRESOLVED" if report["violations"] or not claim.evidence else "VERIFIED_IN_STORED_CORPUS"}))
        return out

    async def update_status(self, claim_id: UUID, payload: ClaimStatusUpdate) -> ClaimOut:
        if self.session is None:
            claim = _CLAIMS.get(claim_id)
            if claim is None:
                raise ClaimValidationError("claim not found")
            updated = claim.model_copy(update={"status": payload.status})
            _CLAIMS[claim_id] = updated
            return updated
        claim = await self.session.get(Claim, claim_id)
        if claim is None:
            raise ClaimValidationError("claim not found")
        claim.verification_status = payload.status
        await self.session.commit()
        await self.session.refresh(claim)
        return self._out(claim)

    @staticmethod
    def _out(claim: Claim) -> ClaimOut:
        status = "unreviewed" if claim.verification_status == "unverified" else claim.verification_status
        return ClaimOut(claim_id=claim.id, claim_text=claim.claim_text, entity_key=claim.entity_key, event_key=claim.event_key, evidence=claim.evidence_json, status=status)
