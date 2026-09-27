"""Evaluation sets/labels/runs, operational metrics, and the scheduler's embedding step (PDF s.13)."""
from __future__ import annotations

import asyncio

import pytest

from app.config.scheduler import SCHEDULER_CONFIG
from app.services import background_scheduler as sched
from app.services import embeddings as emb
from tests.ai_fakes import ConceptEmbeddings
from tests.test_article_search import _seed


@pytest.fixture(autouse=True)
def _reset():
    yield
    emb.set_provider(None)


def _corpus(db_engine):
    return _seed(db_engine, [
        {"title": "JCB launches new excavator range", "summary": "The excavator line targets quarries.", "entity": "JCB", "source": "S1"},
        {"title": "JCB reports quarterly revenue", "summary": "Revenue rose.", "entity": "JCB", "source": "S2"},
        {"title": "ACE announces crane facility", "summary": "Crane capacity expands.", "entity": "ACE", "source": "S3"},
        {"title": "JCB used digger listing", "summary": "Rejected listing.", "entity": "JCB", "status": "rejected", "gate": False, "source": "S4"},
    ])


def _mk(admin_client, kind, name="set1"):
    return admin_client.post("/engine/evaluation/sets", json={"name": name, "kind": kind}).json()["id"]


def test_retrieval_set_reports_not_measured_without_labels_and_compares_modes_with_labels(admin_client, db_engine) -> None:
    ids = _corpus(db_engine)
    set_id = _mk(admin_client, "retrieval")
    empty = admin_client.post(f"/engine/evaluation/sets/{set_id}/run", json={"modes": ["lexical"]}).json()
    assert empty["runs"][0]["metrics"]["status"] == "not measured"
    labels = [{"subject_key": "JCB excavator", "item_key": ids["JCB launches new excavator range"], "label": "relevant", "grade": 2},
              {"subject_key": "JCB excavator", "item_key": ids["JCB reports quarterly revenue"], "label": "not_relevant", "grade": 0}]
    assert admin_client.put(f"/engine/evaluation/sets/{set_id}/labels", json=labels).json() == {"upserted": 2}
    admin_client.post("/engine/index/chunks")
    emb.set_provider(ConceptEmbeddings())
    admin_client.post("/engine/index/embed")
    run = admin_client.post(f"/engine/evaluation/sets/{set_id}/run", json={}).json()
    by_mode = {r["mode"]: r["metrics"] for r in run["runs"]}
    assert set(by_mode) == {"lexical", "semantic", "hybrid"} and all(m["mode_used"] == mode for mode, m in by_mode.items())
    lex = by_mode["lexical"]["aggregate"]
    assert lex["queries_measured"] == 1 and lex["mrr"] == 1.0 and lex["precision@5"] == 0.2
    runs = admin_client.get(f"/engine/evaluation/runs?set_id={set_id}").json()
    assert len(runs) == 4 and runs[0]["corpus_digest"] == run["corpus_digest"]


def test_semantic_mode_without_a_provider_is_reported_as_a_lexical_fallback(admin_client, db_engine) -> None:
    ids = _corpus(db_engine)
    set_id = _mk(admin_client, "retrieval")
    admin_client.put(f"/engine/evaluation/sets/{set_id}/labels", json=[{"subject_key": "excavator", "item_key": ids["JCB launches new excavator range"], "label": "relevant"}])
    run = admin_client.post(f"/engine/evaluation/sets/{set_id}/run", json={"modes": ["hybrid"]}).json()
    assert run["runs"][0]["metrics"]["mode_used"] == "lexical" and run["runs"][0]["metrics"]["fallbacks_to_lexical"] == 1


def test_relevance_set_computes_per_client_precision_recall_from_human_labels(admin_client, db_engine) -> None:
    ids = _corpus(db_engine)
    set_id = _mk(admin_client, "relevance")
    assert admin_client.post(f"/engine/evaluation/sets/{set_id}/run", json={}).json()["runs"][0]["metrics"]["status"] == "not measured"
    admin_client.put(f"/engine/evaluation/sets/{set_id}/labels", json=[
        {"subject_key": "JCB", "item_key": ids["JCB launches new excavator range"], "label": "relevant"},
        {"subject_key": "JCB", "item_key": ids["JCB reports quarterly revenue"], "label": "not_relevant"},
        {"subject_key": "JCB", "item_key": ids["JCB used digger listing"], "label": "relevant"},
    ])
    m = admin_client.post(f"/engine/evaluation/sets/{set_id}/run", json={}).json()["runs"][0]["metrics"]
    jcb = m["per_client"]["JCB"]
    assert (jcb["tp"], jcb["fp"], jcb["fn"], jcb["tn"]) == (1, 1, 1, 0) and jcb["precision"] == 0.5 and jcb["recall"] == 0.5
    assert m["wrongly_rejected_estimate"] == {"labelled_rejected": 1, "human_says_relevant": 1}


def test_taxonomy_set_scores_labels_against_human_yes_no(admin_client, db_engine) -> None:
    ids = _corpus(db_engine)
    admin_client.post("/engine/index/labels")
    set_id = _mk(admin_client, "taxonomy")
    art = ids["JCB launches new excavator range"]
    admin_client.put(f"/engine/evaluation/sets/{set_id}/labels", json=[
        {"subject_key": art, "item_key": "product_business_movement", "label": "yes"}, {"subject_key": art, "item_key": "competitor", "label": "no"},
        {"subject_key": art, "item_key": "research", "label": "yes"}])
    m = admin_client.post(f"/engine/evaluation/sets/{set_id}/run", json={}).json()["runs"][0]["metrics"]["per_label"]
    assert m["product_business_movement"]["tp"] == 1 and m["product_business_movement"]["precision"] == 1.0
    assert m["competitor"]["tn"] == 1 and m["competitor"]["precision"] == "not measured"
    assert m["research"]["fn"] == 1 and m["research"]["recall"] == 0.0


def test_evaluation_validation(admin_client) -> None:
    assert admin_client.post("/engine/evaluation/sets", json={"name": "x", "kind": "bogus"}).status_code == 422
    admin_client.post("/engine/evaluation/sets", json={"name": "dup", "kind": "retrieval"})
    assert admin_client.post("/engine/evaluation/sets", json={"name": "dup", "kind": "retrieval"}).status_code == 422
    assert admin_client.put("/engine/evaluation/sets/00000000-0000-0000-0000-000000000000/labels", json=[]).status_code == 404


def test_evaluation_writes_need_admin(viewer_client) -> None:
    assert viewer_client.post("/engine/evaluation/sets", json={"name": "n", "kind": "retrieval"}).status_code == 403
    assert viewer_client.get("/engine/evaluation/sets").status_code == 200 and viewer_client.get("/engine/evaluation/operations").status_code == 200


def test_operations_metrics_are_measured_or_not_measured_never_invented(admin_client, db_engine) -> None:
    empty = admin_client.get("/engine/evaluation/operations").json()
    assert empty["fetch"]["success_rate"] == "not measured" and empty["evidence"]["claim_quote_support_rate"] == "not measured"
    assert "not accuracy" in empty["accuracy_note"]
    _corpus(db_engine)
    ops = admin_client.get("/engine/evaluation/operations").json()
    assert ops["pipeline"]["accepted"] == 3 and ops["pipeline"]["rejected"] == 1 and ops["llm"]["calls"] == 0


def test_scheduler_cycle_embeds_new_chunks_only_when_a_provider_is_configured(admin_client, db_engine, monkeypatch) -> None:
    _corpus(db_engine)
    admin_client.post("/engine/index/chunks")
    monkeypatch.setattr(sched, "google_specs", lambda client=None: [])
    monkeypatch.setattr(SCHEDULER_CONFIG, "registry_enabled", False)
    monkeypatch.setattr(SCHEDULER_CONFIG, "theme_discovery_enabled", False)
    summary = asyncio.run(sched.run_scheduled_pipeline())
    assert "embeddings" not in summary
    emb.set_provider(ConceptEmbeddings())
    summary = asyncio.run(sched.run_scheduled_pipeline())
    assert summary["embeddings"]["status"] == "ok" and summary["embeddings"]["embedded"] >= 3 and summary["failed_sources"] == []
    emb.set_provider(ConceptEmbeddings(fail=True))
    admin_client.post("/engine/index/chunks")  # nothing new; force a pending chunk
    asyncio.run(_add_chunk(db_engine))
    summary = asyncio.run(sched.run_scheduled_pipeline())
    assert summary["embeddings"]["status"] == "error" and any("embeddings" in f for f in summary["failed_sources"])


async def _add_chunk(db_engine):
    from sqlalchemy import text
    async with db_engine.begin() as c:
        await c.execute(text("update document_chunks set embedding = null, embedding_model = null where id = (select id from document_chunks limit 1)"))
