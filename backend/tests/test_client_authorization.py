"""Regression tests for client-level authorization: non-admin users are default-deny and see only granted clients.

Covers every read path that previously ignored client scoping (article search, evidence search, similar-article
filtering, fact-check coverage, topic briefing, stored insights, recommendations, recall queue, client summary, stored
briefings, profiles) plus the admin grant endpoint. Admin behaviour must stay unrestricted.
"""
from __future__ import annotations

import asyncio
import json
import uuid

from sqlalchemy import text

from app.api.deps import get_current_user
from app.main import app
from tests.conftest import _fake_user, _persist_user
from tests.test_article_search import _seed


def _corpus(db_engine) -> dict[str, str]:
    return _seed(db_engine, [
        {"title": "JCB launches hydrogen excavator for Indian builders", "summary": "JCB excavator news.", "entity": "JCB", "source": "Trade Daily"},
        {"title": "ACE launches electric excavator crane for builders", "summary": "ACE crane excavator news.", "entity": "ACE", "source": "Trade Daily"},
        {"title": "Excavator autonomy paper from robotics lab", "summary": "Research on excavator autonomy.", "entity": "Research", "source": "Arxiv"},
    ])


def _exec(db_engine, sql: str, **params) -> None:
    async def run():
        async with db_engine.begin() as c:
            await c.execute(text(sql), params)
    asyncio.run(run())


def _titles(response) -> set[str]:
    assert response.status_code == 200, response.text
    return {r["title"] for r in response.json()["results"]}


JCB_T = "JCB launches hydrogen excavator for Indian builders"
ACE_T = "ACE launches electric excavator crane for builders"
RESEARCH_T = "Excavator autonomy paper from robotics lab"


def test_viewer_without_any_grant_sees_no_client_data(viewer_factory, db_engine) -> None:
    _corpus(db_engine)
    client, _ = viewer_factory(None)
    assert client.get("/intelligence/clients").json() == []
    assert client.get("/intelligence/clients/JCB/view").status_code == 403
    assert client.get("/search/articles", params={"q": "excavator", "entity_key": "JCB"}).status_code == 403
    titles = _titles(client.get("/search/articles", params={"q": "excavator"}))
    assert JCB_T not in titles and ACE_T not in titles
    assert RESEARCH_T in titles  # not attributed to any client, so not client data


def test_empty_grant_list_is_also_deny(viewer_factory, db_engine) -> None:
    _corpus(db_engine)
    client, _ = viewer_factory([])
    titles = _titles(client.get("/search/articles", params={"q": "excavator"}))
    assert JCB_T not in titles and ACE_T not in titles


def test_scoped_viewer_search_and_evidence_return_only_granted_clients(viewer_factory, db_engine) -> None:
    _corpus(db_engine)
    client, _ = viewer_factory(["JCB"])
    for path in ("/search/articles", "/search/articles/evidence"):
        titles = _titles(client.get(path, params={"q": "excavator"}))
        assert JCB_T in titles and ACE_T not in titles, path
        assert client.get(path, params={"q": "excavator", "entity_key": "ACE"}).status_code == 403
        assert _titles(client.get(path, params={"q": "excavator", "entity_key": "jcb"})) == {JCB_T}
    for mode in ("semantic", "hybrid"):  # falls back to lexical without embeddings; scope must still apply
        assert ACE_T not in _titles(client.get("/search/articles", params={"q": "excavator", "mode": mode}))


def test_admin_search_is_unrestricted(admin_client, db_engine) -> None:
    _corpus(db_engine)
    titles = _titles(admin_client.get("/search/articles", params={"q": "excavator"}))
    assert {JCB_T, ACE_T, RESEARCH_T} <= titles


def test_fact_check_coverage_is_scoped(viewer_factory, db_engine) -> None:
    _corpus(db_engine)
    client, _ = viewer_factory(["JCB"])
    body = client.post("/engine/fact-check", json={"input": "launches excavator builders"}).json()
    covered = {m["title"] for m in body["matched_coverage"]}
    assert ACE_T not in covered


def test_topic_briefing_is_scoped(viewer_factory, db_engine) -> None:
    _corpus(db_engine)
    client, _ = viewer_factory(["JCB"])
    briefing = client.get("/intelligence/briefing", params={"q": "excavator"})
    assert briefing.status_code == 200
    assert "ACE launches" not in json.dumps(briefing.json())


def test_recommendations_insights_and_recall_are_scoped(viewer_factory, db_engine) -> None:
    for key in ("jcb", "ace"):
        _exec(db_engine, "insert into recommendations (id, client_key, title, action, rationale) values (gen_random_uuid(), :k, :t, 'a', 'r')",
              k=key, t=f"rec-{key}")
        _exec(db_engine, "insert into insights (id, client_entity_id, title, status) select gen_random_uuid(), id, :t, 'draft' from entities where canonical_key = :k",
              k=key, t=f"insight-{key}")
        _exec(db_engine, "insert into review_items (id, object_type, status, reason) values (gen_random_uuid(), 'recall_candidate', 'open', :r)",
              r=json.dumps({"client": key.upper(), "note": f"recall-{key}"}))
    client, _ = viewer_factory(["JCB"])
    assert {r["title"] for r in client.get("/engine/recommendations").json()} == {"rec-jcb"}
    assert client.get("/engine/recommendations", params={"client": "ACE"}).status_code == 403
    assert {i["title"] for i in client.get("/engine/insights").json()} == {"insight-jcb"}
    assert client.get("/engine/insights", params={"client": "ACE"}).status_code == 403
    assert {r["note"] for r in client.get("/engine/recall").json()} == {"recall-jcb"}
    # the admin still sees everything
    admin = _fake_user("ADMIN")
    app.dependency_overrides[get_current_user] = lambda: admin
    assert {r["title"] for r in client.get("/engine/recommendations").json()} == {"rec-jcb", "rec-ace"}
    assert {i["title"] for i in client.get("/engine/insights").json()} == {"insight-jcb", "insight-ace"}


def test_summary_briefings_profiles_and_admin_reports_are_scoped(viewer_factory, db_engine) -> None:
    _exec(db_engine, "insert into briefings (id, briefing_date, title, payload) values (gen_random_uuid(), current_date, 'Briefing: ACE', :p)",
          p=json.dumps({"scope": {"client": "ACE"}}))
    _exec(db_engine, "insert into briefings (id, briefing_date, title, payload) values (gen_random_uuid(), current_date, 'Briefing: JCB', :p)",
          p=json.dumps({"scope": {"client": "JCB"}}))
    client, _ = viewer_factory(["JCB"])
    assert [c["client"] for c in client.get("/intelligence/clients/summary").json()["clients"]] == ["JCB"]
    assert [b["title"] for b in client.get("/intelligence/briefings").json()] == ["Briefing: JCB"]
    assert [p["client"] for p in client.get("/profiles").json()] == ["JCB"]
    assert client.get("/profiles/JCB").status_code == 200
    assert client.get("/profiles/ACE").status_code == 403
    assert client.get("/profiles/ACE/suggestions").status_code == 403
    assert client.get("/profiles/audit").status_code == 403
    assert client.get("/engine/near-duplicates").status_code == 403


def test_admin_can_grant_and_revoke_client_access(viewer_factory, db_engine) -> None:
    _corpus(db_engine)
    client, viewer = viewer_factory(None)
    assert client.get("/intelligence/clients/JCB/view").status_code == 403
    assert client.put(f"/auth/users/{viewer.id}/clients", json={"clients": ["JCB"]}).status_code == 403  # viewers cannot self-grant

    admin = _fake_user("ADMIN")
    admin.email = "grant-admin@test.local"
    from sqlalchemy.ext.asyncio import async_sessionmaker
    asyncio.run(_persist_user(async_sessionmaker(db_engine, expire_on_commit=False), admin))
    app.dependency_overrides[get_current_user] = lambda: admin
    granted = client.put(f"/auth/users/{viewer.id}/clients", json={"clients": ["jcb india", "JCB"]})
    assert granted.status_code == 200 and granted.json() == {"clients": ["JCB"]}  # alias resolved, de-duplicated
    assert client.put(f"/auth/users/{viewer.id}/clients", json={"clients": ["Nope Corp"]}).status_code == 422
    assert client.put(f"/auth/users/{uuid.uuid4()}/clients", json={"clients": []}).status_code == 404
    assert client.get(f"/auth/users/{viewer.id}/clients").json() == {"clients": ["JCB"]}

    app.dependency_overrides[get_current_user] = lambda: viewer
    assert client.get("/intelligence/clients").json() == ["JCB"]
    assert client.get("/intelligence/clients/ACE/view").status_code == 403

    app.dependency_overrides[get_current_user] = lambda: admin
    assert client.put(f"/auth/users/{viewer.id}/clients", json={"clients": []}).json() == {"clients": []}
    app.dependency_overrides[get_current_user] = lambda: viewer
    assert client.get("/intelligence/clients").json() == []
