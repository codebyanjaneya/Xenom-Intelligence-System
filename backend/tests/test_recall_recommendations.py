"""Recall recovery (the only route back for rejected items), recommendations and capability matching."""
from __future__ import annotations

import asyncio
import json

from sqlalchemy import text

from app.services import embeddings as emb
from app.services import profiles as prof
from app.services.recall_recovery import lexical_similarity
from tests.ai_fakes import ConceptEmbeddings
from tests.test_article_search import _seed
from tests.test_client_intelligence import _corpus as client_corpus


def _q(db_engine, sql, **params):
    async def run():
        async with db_engine.connect() as c:
            return (await c.execute(text(sql), params)).all()
    return asyncio.run(run())


def _reject(db_engine, article_id, reason="NO_ENTITY_EVIDENCE", stage="relevance"):
    async def run():
        async with db_engine.begin() as c:
            await c.execute(text("update articles set score_reason = :r, relevance_score = 0 where id = :i"),
                            {"r": json.dumps({"decision": "rejected", "stage": stage, "reason": reason, "entity": "JCB"}), "i": article_id})
            await c.execute(text("delete from entity_mentions where article_id = :i"), {"i": article_id})
    asyncio.run(run())


def _profile_text(client="JCB", n=4):
    p = prof.code_profile(client)
    from app.services.recall_recovery import profile_terms
    return ", ".join(profile_terms(p)[:n])


def _rejected(db_engine, with_url=True):
    title = f"Industry roundup: {_profile_text()}"
    ids = _seed(db_engine, [
        {"title": title, "summary": "", "entity": "JCB", "status": "rejected", "gate": False, "source": "S1",
         "canonical_url": "https://pub.test/roundup" if with_url else None},
        {"title": "Unrelated cricket score update", "summary": "", "entity": "JCB", "status": "rejected", "gate": False, "source": "S2",
         "canonical_url": "https://pub.test/cricket"},
        {"title": "JCB used machine for sale", "summary": "", "entity": "JCB", "status": "rejected", "gate": False, "source": "S3"},
    ])
    _reject(db_engine, ids[title])
    _reject(db_engine, ids["Unrelated cricket score update"])
    _reject(db_engine, ids["JCB used machine for sale"], reason="HARD_NEGATIVE")
    return ids, title


def test_profile_similarity_is_literal_term_overlap() -> None:
    p = prof.code_profile("JCB")
    score, terms = lexical_similarity(f"Note: {_profile_text()}", p)
    assert score >= 0.5 and len(terms) >= 3
    assert lexical_similarity("Cricket final tonight", p) == (0.0, [])


def test_scan_queues_high_scorers_never_hard_negatives_and_is_idempotent(admin_client, db_engine) -> None:
    ids, title = _rejected(db_engine)
    out = admin_client.post("/engine/recall/scan", json={"client": "JCB"}).json()
    assert out["method"] == "lexical_profile" and out["queued"] == {"JCB": 1}
    queue = admin_client.get("/engine/recall").json()
    assert [q["title"] for q in queue] == [title] and queue[0]["article_status"] == "rejected" and queue[0]["matched_profile_terms"]
    assert admin_client.post("/engine/recall/scan", json={"client": "JCB"}).json()["queued"] == {}
    assert _q(db_engine, "select processing_status from articles where id = :i", i=ids[title]) == [("rejected",)]  # nothing re-admitted yet


def test_accepting_a_candidate_readmits_it_with_provenance_and_mention(admin_client, db_engine) -> None:
    ids, title = _rejected(db_engine)
    admin_client.post("/engine/recall/scan", json={"client": "JCB"})
    item = admin_client.get("/engine/recall").json()[0]
    done = admin_client.post(f"/engine/recall/{item['id']}", json={"accept": True}).json()
    assert done["status"] == "accepted" and done["client"] == "JCB"
    row = _q(db_engine, "select processing_status, quality_gate_passed, score_reason from articles where id = :i", i=ids[title])[0]
    assert row[0] == "accepted" and row[1] is True and json.loads(row[2])["recovered_by"]["method"] == "lexical_profile"
    assert _q(db_engine, "select count(*) from entity_mentions where article_id = :i", i=ids[title])[0][0] == 1
    assert _q(db_engine, "select count(*) from document_chunks where article_id = :i", i=ids[title])[0][0] >= 1
    assert admin_client.get("/engine/recall").json() == [] and len(admin_client.get("/engine/recall?status=resolved").json()) == 1
    assert admin_client.post(f"/engine/recall/{item['id']}", json={"accept": True}).status_code == 409
    assert [h["title"] for h in admin_client.get("/search/articles?q=roundup&entity_key=jcb").json()["results"]] == [title]


def test_candidate_without_a_publisher_url_cannot_be_accepted(admin_client, db_engine) -> None:
    ids, title = _rejected(db_engine, with_url=False)
    admin_client.post("/engine/recall/scan", json={"client": "JCB"})
    item = admin_client.get("/engine/recall").json()[0]
    blocked = admin_client.post(f"/engine/recall/{item['id']}", json={"accept": True})
    assert blocked.status_code == 409 and "publisher URL" in blocked.json()["error"]["message"]
    assert admin_client.post(f"/engine/recall/{item['id']}", json={"accept": False}).json()["status"] == "dismissed"


def test_scan_uses_embedding_similarity_when_a_provider_is_configured(admin_client, db_engine) -> None:
    _rejected(db_engine)
    emb.set_provider(ConceptEmbeddings())
    try:
        out = admin_client.post("/engine/recall/scan", json={"client": "JCB"}).json()
        assert out["method"] == "embedding" and out["threshold"] == 0.55
        assert _q(db_engine, "select count(*) from ai_usage where operation = 'embedding:recall'")[0][0] == 2
    finally:
        emb.set_provider(None)


def test_recall_routes_need_admin_for_writes(viewer_client) -> None:
    assert viewer_client.post("/engine/recall/scan", json={}).status_code == 403
    assert viewer_client.get("/engine/recall").status_code == 200


# ---------------------------------------------------------------------------- recommendations + capability matching
def test_recommendations_persist_as_reviewable_drafts_with_evidence_based_confidence(admin_client, db_engine) -> None:
    client_corpus(db_engine)
    out = admin_client.post("/engine/recommendations/sync", json={"client": "JCB"}).json()
    assert out["created"] >= 1 and out["created"] == out["opportunities"]
    recs = admin_client.get("/engine/recommendations?client=jcb").json()
    r = recs[0]
    assert r["status"] == "draft" and r["origin"] == "rule" and r["generated"] is False and 0 < r["confidence"] <= 0.9
    assert r["action"].startswith("Verify") and r["article_ids"] and r["confidence_basis"]["independent_publishers"] >= 1
    again = admin_client.post("/engine/recommendations/sync", json={"client": "JCB"}).json()
    assert again["created"] == 0 and len(admin_client.get("/engine/recommendations?client=jcb").json()) == len(recs)
    reviewed = admin_client.patch(f"/engine/recommendations/{r['id']}", json={"status": "reviewed"}).json()
    assert reviewed["status"] == "reviewed" and reviewed["reviewed_by"] == "admin@test.local"
    admin_client.post("/engine/recommendations/sync", json={"client": "JCB"})
    assert next(x for x in admin_client.get("/engine/recommendations?client=jcb").json() if x["id"] == r["id"])["status"] == "reviewed"
    assert admin_client.post("/engine/recommendations/sync", json={"client": "nobody"}).status_code == 404


def test_recommendation_writes_are_admin_only(viewer_client) -> None:
    assert viewer_client.post("/engine/recommendations/sync", json={"client": "JCB"}).status_code == 403
    assert viewer_client.get("/engine/recommendations").status_code == 200


def test_capability_matching_is_verified_only_for_verified_capabilities(admin_client, db_engine) -> None:
    client_corpus(db_engine)
    opps = admin_client.get("/intelligence/clients/JCB/view").json()["opportunities"]
    assert opps and {o["capability_match"] for o in opps} == {"UNVERIFIED"} and {o["capability"] for o in opps} == {None}
    stated = next(w for w in opps[0]["title"].split(" - ", 1)[1].split() if len(w) >= 5 and w.isalpha()).lower()  # a word the stated event literally contains
    admin_client.put("/engine/xenom/capabilities", json={"name": stated, "verification_status": "UNVERIFIED"})
    assert {o["capability_match"] for o in admin_client.get("/intelligence/clients/JCB/view").json()["opportunities"]} == {"UNVERIFIED"}
    admin_client.put("/engine/xenom/capabilities", json={"name": stated, "verification_status": "VERIFIED", "source_note": "test data"})
    matched = [o for o in admin_client.get("/intelligence/clients/JCB/view").json()["opportunities"] if o["capability_match"] == "VERIFIED"]
    assert matched and matched[0]["capability"] == stated
    assert admin_client.get("/engine/xenom/capabilities").json()[0]["verification_status"] == "VERIFIED"
