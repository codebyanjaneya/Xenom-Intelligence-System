"""Claims validate against the DB-backed stored-article evidence path (no fixtures). Test DB only."""
from __future__ import annotations

import asyncio

import pytest

from app.schemas.claims import ClaimCreate
from app.schemas.intelligence import IntelligenceRequest
from app.services.claims import ClaimService, ClaimValidationError
from tests.test_article_search import _seed

QUERY = "excavator"


def _setup(client, db_engine):
    """Seed stored articles, run the real extract flow, return (entity_key, event, evidence-of-event)."""
    _seed(db_engine, [
        {"title": "JCB launches autonomous excavator", "summary": "Robotics on site.", "entity": "JCB", "source": "S"},
        {"title": "ISM chips", "summary": "excavator unrelated", "entity": "ISM", "source": "S"},
    ])
    body = client.post("/intelligence/extract", json={"query": QUERY}).json()
    event = body["events"][0]
    return body, event


def _payload(body, event, **overrides):
    payload = {
        "query": QUERY,
        "claim_text": "JCB launched an autonomous excavator.",
        "entity_key": "jcb",
        "event_key": event["event_key"],
        "evidence": [event["evidence"][0]],
    }
    payload.update(overrides)
    return payload


def test_admin_can_create_claim_with_stored_provenance(admin_client, db_engine) -> None:
    body, event = _setup(admin_client, db_engine)
    r = admin_client.post("/claims", json=_payload(body, event))
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["status"] == "unreviewed" and out["entity_key"] == "jcb" and out["event_key"] == event["event_key"]
    ev = out["evidence"][0]
    assert ev["provider"] == "stored_articles" and ev["content_span"] == "JCB launches autonomous excavator"
    assert ev["source_url"].startswith("https://a.test/")  # the stored article, not a fixture URL


def test_entity_only_claim_uses_entity_evidence(admin_client, db_engine) -> None:
    body, event = _setup(admin_client, db_engine)
    jcb = next(e for e in body["entities"] if e["canonical_key"] == "jcb")
    r = admin_client.post("/claims", json=_payload(body, event, event_key=None, evidence=[jcb["evidence"][0]]))
    assert r.status_code == 201, r.text


def test_repeated_claim_creation_is_deterministic(admin_client, db_engine) -> None:
    body, event = _setup(admin_client, db_engine)
    first = admin_client.post("/claims", json=_payload(body, event, claim_text="Deterministic repeated claim."))
    second = admin_client.post("/claims", json=_payload(body, event, claim_text="Deterministic repeated claim."))
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()


def test_claim_requires_reference_and_evidence() -> None:
    ev = {"source_url": "u", "provider": "p", "title": "t", "snippet": "s", "published_at": None,
          "discovered_at": "2026-01-01T00:00:00Z", "relevance_score": 1.0}
    with pytest.raises(ValueError, match="reference"):
        ClaimCreate(query=QUERY, claim_text="Missing reference", evidence=[ev])
    with pytest.raises(ValueError):
        ClaimCreate(query=QUERY, claim_text="Missing evidence", entity_key="jcb")


def test_unknown_reference_and_unsupported_evidence_are_rejected(admin_client, db_engine) -> None:
    body, event = _setup(admin_client, db_engine)
    r = admin_client.post("/claims", json=_payload(body, event, entity_key="unknown entity"))
    assert r.status_code == 422 and "unknown entity" in r.text
    r = admin_client.post("/claims", json=_payload(body, event, event_key="ev-doesnotexist"))
    assert r.status_code == 422 and "unknown event" in r.text
    other_url = {**event["evidence"][0], "source_url": "https://unknown.test/page"}
    r = admin_client.post("/claims", json=_payload(body, event, evidence=[other_url]))
    assert r.status_code == 422 and "evidence" in r.text
    invented = {**event["evidence"][0], "content_span": "JCB acquired the moon."}          # span not in stored text
    r = admin_client.post("/claims", json=_payload(body, event, evidence=[invented]))
    assert r.status_code == 422 and "evidence" in r.text


def test_claim_for_query_without_stored_articles_is_rejected(admin_client, db_engine) -> None:
    body, event = _setup(admin_client, db_engine)
    r = admin_client.post("/claims", json=_payload(body, event, query="zzzunmatched"))
    assert r.status_code == 422


def test_claim_status_updates_and_listing(admin_client, db_engine) -> None:
    body, event = _setup(admin_client, db_engine)
    created = admin_client.post("/claims", json=_payload(body, event, claim_text="Status transition claim."))
    claim_id = created.json()["claim_id"]
    verified = admin_client.patch(f"/claims/{claim_id}/status", json={"status": "verified"})
    rejected = admin_client.patch(f"/claims/{claim_id}/status", json={"status": "rejected"})
    assert created.status_code == 201 and verified.json()["status"] == "verified" and rejected.json()["status"] == "rejected"
    assert any(c["claim_id"] == claim_id for c in admin_client.get("/claims").json())


def test_viewer_rejected(viewer_client) -> None:
    assert viewer_client.post("/claims", json={"query": QUERY, "claim_text": "x", "entity_key": "jcb",
                                               "evidence": []}).status_code in (403, 422)
    assert viewer_client.get("/claims").status_code == 403


def test_anonymous_rejected(anon_client) -> None:
    assert anon_client.get("/claims").status_code in (401, 403)


def test_service_without_a_source_refuses_and_accepts_an_injected_one() -> None:
    payload = ClaimCreate(query=QUERY, claim_text="c", entity_key="jcb", evidence=[{
        "source_url": "u", "provider": "p", "title": "t", "snippet": "s", "published_at": None,
        "discovered_at": "2026-01-01T00:00:00Z", "relevance_score": 1.0}])
    with pytest.raises(ClaimValidationError, match="no intelligence source"):
        asyncio.run(ClaimService().create(payload))

    class Stub:
        async def extract(self, request: IntelligenceRequest):
            raise ValueError("stub failure")

    with pytest.raises(ClaimValidationError, match="stub failure"):
        asyncio.run(ClaimService(intelligence=Stub()).create(payload))
