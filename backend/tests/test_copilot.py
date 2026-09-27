from __future__ import annotations

from tests.ai_fakes import ScriptedLLM
from app.services import llm


def test_copilot_uses_controlled_client_data_and_citations(admin_client, db_engine):
    from tests.test_client_intelligence import _corpus
    _corpus(db_engine)
    llm.set_provider(ScriptedLLM({
        "answer": "The stored intelligence contains supported client signals.",
        "insufficient_evidence": False,
        "citations": [{"title": "Stored source", "url": "https://siteA.test/1", "date": "2026-09-20", "quote": "JCB launches backhoe alpha model for farms"}],
    }))
    response = admin_client.post("/copilot", json={"query": "What changed for JCB?"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["intent"] == "client_briefing"
    assert body["answer"].startswith("The stored intelligence")
    assert body["citations"][0]["url"] == "https://siteA.test/1"
    assert body["data"]
    llm.set_provider(None)


def test_copilot_requires_admin(viewer_client):
    response = viewer_client.post("/copilot", json={"query": "Show recent research"})
    assert response.status_code == 403


def test_copilot_isolates_jcb_and_rejects_empty_ace(admin_client, db_engine):
    from tests.test_client_intelligence import _corpus
    _corpus(db_engine)
    llm.set_provider(ScriptedLLM({
        "answer": "JCB has verified stored signals.",
        "insufficient_evidence": False,
        "citations": [{"title": "JCB source", "url": "https://siteA.test/1"}],
    }))
    jcb = admin_client.post("/copilot", json={"query": "What changed for JCB?"}).json()
    assert jcb["client"] == "JCB"
    assert jcb["data"]["scope"] == {"client": "JCB"}
    assert all(event["entity_keys"] == ["jcb"] for event in jcb["data"]["key_events"])

    ace = admin_client.post("/copilot", json={"query": "What changed for ACE?"}).json()
    assert ace["client"] == "ACE"
    assert ace["insufficient_evidence"] is True
    assert ace["citations"] == [] and ace["data"] == {}
    llm.set_provider(None)


def test_copilot_followup_keeps_client_scope_and_generic_is_clean(admin_client, db_engine):
    from tests.test_client_intelligence import _corpus
    _corpus(db_engine)
    provider = ScriptedLLM(
        {"answer": "JCB evidence is available.", "insufficient_evidence": False,
         "citations": [{"title": "JCB source", "url": "https://siteA.test/1"}]},
        {"answer": "JCB opportunities remain evidence-backed possibilities.", "insufficient_evidence": False,
         "citations": [{"title": "JCB source", "url": "https://siteF.test/6"}]},
    )
    llm.set_provider(provider)
    followup = admin_client.post("/copilot", json={
        "query": "What about opportunities?",
        "history": [{"query": "What changed for JCB?", "answer": "JCB evidence is available.",
                     "intent": "client_briefing", "client": "JCB"}],
    }).json()
    assert followup["client"] == "JCB"
    assert followup["data"]["scope"] == {"client": "JCB"}
    assert all(event["entity_keys"] == ["jcb"] for event in followup["data"]["key_events"])
    assert "JCB" in provider.calls[-1][1]

    generic = admin_client.post("/copilot", json={"query": "What is XENOM?"}).json()
    assert generic["intent"] == "platform"
    assert generic["insufficient_evidence"] is True
    assert generic["citations"] == [] and generic["data"] == {}
    llm.set_provider(None)


def test_copilot_unknown_company_never_falls_back_to_unrelated_search(admin_client, db_engine):
    from tests.test_client_intelligence import _corpus
    _corpus(db_engine)
    llm.set_provider(ScriptedLLM({"answer": "should not be called", "insufficient_evidence": False, "citations": []}))
    body = admin_client.post("/copilot", json={"query": "What changed for company called NonexistentCo?"}).json()
    assert body["insufficient_evidence"] is True
    assert body["citations"] == [] and body["data"] == {}
    llm.set_provider(None)


def test_copilot_unknown_company_does_not_inherit_previous_client(admin_client, db_engine):
    from tests.test_client_intelligence import _corpus
    _corpus(db_engine)
    body = admin_client.post("/copilot", json={
        "query": "What changed for company called NonexistentCo?",
        "history": [{"query": "What changed for ACE?", "answer": "", "intent": "client_briefing", "client": "ACE"}],
    }).json()
    assert body["client"] is None
    assert body["insufficient_evidence"] is True
    assert body["citations"] == [] and body["data"] == {}
