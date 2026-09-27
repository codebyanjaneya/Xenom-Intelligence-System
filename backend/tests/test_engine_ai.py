"""Chunks, embeddings, hybrid retrieval with lexical fallback, LLM enrichment guarantees, AI usage (PDF s.8, s.10, s.13)."""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from app.services import embeddings as emb
from app.services import llm
from tests.ai_fakes import ConceptEmbeddings, ScriptedLLM
from tests.test_article_search import _seed


@pytest.fixture(autouse=True)
def _reset_providers():
    yield
    emb.set_provider(None)
    llm.set_provider(None)


def _q(db_engine, sql, **params):
    async def run():
        async with db_engine.connect() as c:
            return (await c.execute(text(sql), params)).all()
    return asyncio.run(run())


def _corpus(db_engine):
    return _seed(db_engine, [
        {"title": "JCB launches new excavator range for Indian quarries", "summary": "The excavator line targets quarry operators.", "entity": "JCB", "source": "S1"},
        {"title": "JCB reports quarterly revenue and profit", "summary": "Revenue rose in the quarter.", "entity": "JCB", "source": "S2"},
        {"title": "ACE announces new crane facility", "summary": "Crane capacity expands.", "entity": "ACE", "source": "S3"},
        {"title": "JCB digger recall rejected item", "summary": "A digger story that was rejected.", "entity": "JCB", "status": "rejected", "gate": False, "source": "S4"},
    ])


def _index(admin_client):
    assert admin_client.post("/engine/index/chunks").json()["articles_chunked"] == 3  # accepted only


def test_capabilities_are_honest_without_providers(admin_client) -> None:
    caps = admin_client.get("/engine/capabilities").json()
    assert caps["embeddings"]["status"] == "not_configured" and caps["llm"]["status"] == "not_configured"
    assert caps["hybrid_search"]["status"] == "lexical_fallback" and caps["lexical_search"]["status"] == "active"
    assert admin_client.post("/engine/index/embed").json()["status"] == "not_configured"


def test_semantic_and_hybrid_fall_back_to_lexical_when_vectors_are_unavailable(admin_client, db_engine) -> None:
    _corpus(db_engine)
    lexical = admin_client.get("/search/articles?q=excavator").json()
    for mode in ("semantic", "hybrid"):
        body = admin_client.get(f"/search/articles?q=excavator&mode={mode}").json()
        assert body["mode"] == "lexical" and body["mode_requested"] == mode and "no embedding provider" in body["fallback_reason"]
        assert [h["article_id"] for h in body["results"]] == [h["article_id"] for h in lexical["results"]]
    assert admin_client.get("/search/articles?q=excavator&mode=bogus").status_code == 422


def test_embedding_only_covers_accepted_articles_and_records_the_model(admin_client, db_engine) -> None:
    _corpus(db_engine)
    _index(admin_client)
    emb.set_provider(ConceptEmbeddings())
    done = admin_client.post("/engine/index/embed").json()
    assert done["status"] == "ok" and done["embedded"] >= 3 and done["model"] == "concept-v1"
    rows = _q(db_engine, "select count(*), count(distinct embedding_model) from document_chunks where embedding is not null")
    assert rows[0][1] == 1
    assert _q(db_engine, "select count(*) from document_chunks c join articles a on a.id = c.article_id where a.processing_status = 'rejected'")[0][0] == 0
    assert _q(db_engine, "select count(*) from ai_usage where operation = 'embedding:chunks' and status = 'ok'")[0][0] >= 1
    emb.set_provider(ConceptEmbeddings(model="concept-v2"))  # a different model re-embeds everything
    assert admin_client.post("/engine/index/embed").json()["embedded"] == rows[0][0]
    assert _q(db_engine, "select distinct embedding_model from document_chunks where embedding is not null") == [("concept-v2",)]


def test_semantic_search_finds_a_hit_with_no_shared_word_and_cites_the_stored_chunk(admin_client, db_engine) -> None:
    _corpus(db_engine)
    _index(admin_client)
    emb.set_provider(ConceptEmbeddings())
    admin_client.post("/engine/index/embed")
    assert admin_client.get("/search/articles?q=earthmover").json()["count"] == 0  # lexical finds nothing
    body = admin_client.get("/search/articles/evidence?q=earthmover&mode=semantic").json()
    assert body["mode"] == "semantic" and body["fallback_reason"] is None
    top = body["results"][0]
    assert "excavator" in top["title"].lower() and top["retrieval"]["vector_similarity"] > 0 and top["retrieval"]["model"] == "concept-v1"
    span = top["spans"][0]
    assert span["quote"] and "earthmover" not in span["terms"]  # no literal query term is claimed; the span is stored text
    art = _q(db_engine, f"select {span['field']} from articles where id = :i", i=top["article_id"])[0][0]
    assert art[span["start"]:span["end"]] == span["quote"]
    titles = [r["title"] for r in body["results"]]
    assert not any("rejected" in t.lower() for t in titles)  # semantic search never resurrects a rejected item


def test_hybrid_fuses_both_rankings_and_filters_apply_to_the_vector_path(admin_client, db_engine) -> None:
    _corpus(db_engine)
    _index(admin_client)
    emb.set_provider(ConceptEmbeddings())
    admin_client.post("/engine/index/embed")
    hybrid = admin_client.get("/search/articles?q=JCB%20excavator%20digger&mode=hybrid").json()
    assert hybrid["mode"] == "hybrid" and hybrid["results"][0]["retrieval"]["lexical_rank"] and hybrid["results"][0]["retrieval"]["vector_rank"]
    assert "excavator" in hybrid["results"][0]["title"].lower()
    again = admin_client.get("/search/articles?q=JCB%20excavator%20digger&mode=hybrid").json()
    assert [h["article_id"] for h in again["results"]] == [h["article_id"] for h in hybrid["results"]]  # deterministic
    only_ace = admin_client.get("/search/articles?q=crane&mode=semantic&entity_key=ace").json()
    assert {h["client"] for h in only_ace["results"]} == {"ACE"}
    none = admin_client.get("/search/articles?q=excavator&mode=semantic&entity_key=ism").json()
    assert none["mode"] == "lexical" and none["count"] == 0 and "no embedded chunks" in none["fallback_reason"]


def test_provider_failure_is_logged_and_search_still_answers(admin_client, db_engine) -> None:
    _corpus(db_engine)
    _index(admin_client)
    emb.set_provider(ConceptEmbeddings(fail=True))
    body = admin_client.get("/search/articles?q=excavator&mode=hybrid").json()
    assert body["mode"] == "lexical" and "embedding unavailable" in body["fallback_reason"] and body["count"] >= 1
    usage = admin_client.get("/engine/ai-usage").json()
    assert usage["recent_errors"][0]["operation"] == "embedding:query" and "down" in usage["recent_errors"][0]["error"]


# ------------------------------------------------------------------------------------------------ LLM guarantees
def _accepted(db_engine):
    ids = _seed(db_engine, [{"title": "JCB India opens Pune plant", "summary": "JCB India opened a new plant in Pune. The plant will employ 500 people.",
                             "entity": "JCB", "source": "S1"}])
    return next(iter(ids.values()))


def test_llm_is_not_called_or_simulated_when_unconfigured(admin_client, db_engine) -> None:
    article = _accepted(db_engine)
    for path in (f"/engine/llm/claims/{article}", f"/engine/llm/labels/{article}"):
        r = admin_client.post(path)
        assert r.status_code == 503 and "not configured" in r.json()["error"]["message"]
    assert admin_client.post("/engine/llm/synthesize", json={"client": "JCB"}).status_code == 200  # nothing to synthesize: no call needed
    assert admin_client.post("/engine/llm/synthesize", json={"client": "JCB"}).json()["status"] == "insufficient_evidence"


def test_extracted_claims_need_a_verbatim_quote_and_the_rest_are_dropped(admin_client, db_engine) -> None:
    article = _accepted(db_engine)
    fake = ScriptedLLM({"claims": [
        {"claim": "JCB India opened a plant in Pune", "quote": "JCB India opened a new plant in Pune.", "claim_type": "announcement"},
        {"claim": "The plant will employ 5000 people", "quote": "The plant will employ 5000 people.", "claim_type": "forecast"},
        {"claim": "JCB is the largest maker", "quote": "JCB is the largest maker in India"},
    ]})
    llm.set_provider(fake)
    out = admin_client.post(f"/engine/llm/claims/{article}").json()
    assert out["status"] == "ok" and len(out["claims"]) == 1 and out["dropped_without_verified_quote"] == 2
    claims = admin_client.get("/claims").json()
    assert len(claims) == 1 and claims[0]["status"] == "unreviewed" and claims[0]["provenance_status"] == "VERIFIED_IN_STORED_CORPUS"
    assert claims[0]["evidence"][0]["content_span"] == "JCB India opened a new plant in Pune."
    usage = _q(db_engine, "select operation, prompt_version, status, total_tokens, model from ai_usage")
    assert usage == [("llm:extract_claims", "2", "ok", 160, "scripted-1")]


def test_invalid_model_output_and_provider_errors_are_logged_not_stored(admin_client, db_engine) -> None:
    article = _accepted(db_engine)
    llm.set_provider(ScriptedLLM("this is not json"))
    assert admin_client.post(f"/engine/llm/claims/{article}").status_code == 502
    llm.set_provider(ScriptedLLM({"claims": "nope"}))
    assert admin_client.post(f"/engine/llm/claims/{article}").status_code == 502
    llm.set_provider(ScriptedLLM(fail=True))
    assert admin_client.post(f"/engine/llm/claims/{article}").status_code == 503
    assert [r[0] for r in _q(db_engine, "select status from ai_usage order by created_at")] == ["invalid_output", "invalid_output", "error"]
    assert _q(db_engine, "select count(*) from claims")[0][0] == 0


def test_daily_budget_stops_llm_calls(admin_client, db_engine, monkeypatch) -> None:
    article = _accepted(db_engine)
    monkeypatch.setattr(llm.get_settings(), "llm_daily_call_budget", 1)
    fake = ScriptedLLM({"claims": []}, {"claims": []})
    llm.set_provider(fake)
    assert admin_client.post(f"/engine/llm/claims/{article}").status_code == 200
    blocked = admin_client.post(f"/engine/llm/claims/{article}")
    assert blocked.status_code == 503 and "budget" in blocked.json()["error"]["message"] and len(fake.calls) == 1


def test_synthesis_only_keeps_sentences_that_cite_supplied_claims(admin_client, db_engine) -> None:
    article = _accepted(db_engine)
    llm.set_provider(ScriptedLLM({"claims": [{"claim": "JCB India opened a plant in Pune", "quote": "JCB India opened a new plant in Pune."}]}))
    admin_client.post(f"/engine/llm/claims/{article}")
    fake = ScriptedLLM({"sentences": [
        {"text": "JCB India added Pune plant capacity.", "claim_refs": ["C1"]},
        {"text": "JCB will dominate the market.", "claim_refs": []},
        {"text": "Demand doubled.", "claim_refs": ["C9"]},
    ]})
    llm.set_provider(fake)
    out = admin_client.post("/engine/llm/synthesize", json={"client": "JCB"}).json()
    assert out["status"] == "ok" and out["generated"] is True and out["kept_sentences"] == 1 and out["removed_uncited_sentences"] == 2
    assert out["stored"]["status"] == "draft" and 0 < out["confidence"] <= 0.9
    rows = _q(db_engine, "select title, body, status, insight_type from insights")
    assert rows == [("Generated synthesis: JCB", "JCB India added Pune plant capacity. [C1]", "draft", "llm_synthesis")]
    assert _q(db_engine, "select count(*) from insight_claims")[0][0] == 1
    llm.set_provider(ScriptedLLM({"sentences": [{"text": "Unsupported claim.", "claim_refs": []}]}))
    nothing = admin_client.post("/engine/llm/synthesize", json={"client": "JCB", "kind": "recommendation"}).json()
    assert nothing["status"] == "insufficient_evidence" and nothing["stored"] is None
    assert _q(db_engine, "select count(*) from recommendations")[0][0] == 0


def test_borderline_adjudication_is_queued_for_a_human_and_never_changes_the_article(admin_client, db_engine) -> None:
    import json
    ids = _seed(db_engine, [{"title": "ACE rises", "summary": "The crane maker ACE saw strong demand.", "entity": "ACE", "status": "rejected", "gate": False, "source": "S1"}])
    article = next(iter(ids.values()))

    async def make_borderline():
        async with db_engine.begin() as c:
            await c.execute(text("update articles set score_reason = :r where id = :i"),
                            {"r": json.dumps({"decision": "rejected", "stage": "relevance", "reason": "WEAK_ACRONYM_NO_CONTEXT", "entity": "ACE"}), "i": article})
    asyncio.run(make_borderline())
    llm.set_provider(ScriptedLLM({"decision": "relevant", "reason": "crane maker named", "quote": "The crane maker ACE saw strong demand."}))
    out = admin_client.post(f"/engine/llm/adjudicate/{article}").json()
    assert out["decision"] == "relevant" and out["queued_for_review"] is True
    assert _q(db_engine, "select processing_status from articles where id = :i", i=article) == [("rejected",)]
    assert _q(db_engine, "select object_type, status from review_items") == [("llm_adjudication", "open")]
    llm.set_provider(ScriptedLLM({"decision": "relevant", "reason": "x", "quote": "invented sentence"}))
    assert admin_client.post(f"/engine/llm/adjudicate/{article}").json()["decision"] == "insufficient_evidence"  # unverifiable quote
    fresh = _accepted(db_engine)
    assert admin_client.post(f"/engine/llm/adjudicate/{fresh}").json()["status"] == "not_borderline"


def test_engine_write_routes_are_admin_only(viewer_client) -> None:
    for path in ("/engine/index/chunks", "/engine/index/embed", "/engine/index/labels", "/engine/llm/synthesize"):
        assert viewer_client.post(path, json={"client": "JCB"}).status_code == 403
    assert viewer_client.get("/engine/capabilities").status_code == 200 and viewer_client.get("/engine/ai-usage").status_code == 200


def test_similar_articles_and_near_duplicates_are_reported_never_merged(admin_client, db_engine) -> None:
    ids = _seed(db_engine, [
        {"title": "JCB launches new excavator range", "summary": "", "entity": "JCB", "source": "S1"},
        {"title": "JCB launches new excavator range today", "summary": "", "entity": "JCB", "source": "S2"},
        {"title": "ACE announces crane facility", "summary": "", "entity": "ACE", "source": "S3"},
    ])
    a = ids["JCB launches new excavator range"]
    assert admin_client.get(f"/search/articles/{a}/similar").json()["status"] == "NO_EMBEDDINGS"
    assert admin_client.get("/engine/near-duplicates").json()["pairs"] == []
    admin_client.post("/engine/index/chunks")
    emb.set_provider(ConceptEmbeddings())
    admin_client.post("/engine/index/embed")
    similar = admin_client.get(f"/search/articles/{a}/similar?limit=2").json()
    assert similar["status"] == "ok" and similar["results"][0]["title"] == "JCB launches new excavator range today" and similar["results"][0]["similarity"] > 0.8
    dup = admin_client.get("/engine/near-duplicates?threshold=0.8").json()
    assert len(dup["pairs"]) == 1 and {dup["pairs"][0]["a"]["title"], dup["pairs"][0]["b"]["title"]} == {"JCB launches new excavator range", "JCB launches new excavator range today"}
    assert "Nothing is merged" in dup["note"]
    assert _q(db_engine, "select count(*) from articles where processing_status = 'accepted'")[0][0] == 3  # nothing discarded
