"""Regression tests: a viewer must not learn anything about an unauthorized client's article by supplying its ID.

Covers PUT /articles/{id}/saved, GET /articles/saved (defense in depth after access is revoked) and
GET /search/articles/{id}/similar (access checked before any similarity retrieval). Embeddings come from the test-only
ConceptEmbeddings provider; no network or real model is used.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import text

from app.api.deps import get_current_user
from app.main import app
from app.services import embeddings as emb
from app.services import retrieval
from tests.ai_fakes import ConceptEmbeddings
from tests.conftest import _fake_user
from tests.test_article_search import _seed

ACE_T = "ACE crane chip plant expansion"
ACE2_T = "ACE crane semiconductor chip order"
JCB_T = "JCB chip wafer excavator"
RES_T = "Wafer chip research from university lab"


@pytest.fixture
def corpus(_db_override, db_engine):  # after the per-test truncate, never before it
    return _seed(db_engine, [
        {"title": ACE_T, "summary": "ACE crane chip wafer fab.", "entity": "ACE", "source": "S1"},
        {"title": ACE2_T, "summary": "ACE chip fab wafer deal.", "entity": "ACE", "source": "S2"},
        {"title": JCB_T, "summary": "JCB chip fab wafer.", "entity": "JCB", "source": "S3"},
        {"title": RES_T, "summary": "Chip wafer fab research.", "entity": "Research", "source": "S4"},  # no client attribution
    ])


@pytest.fixture
def embedded(corpus, viewer_factory):
    """Chunk + embed the corpus as an admin, then hand back a JCB-only viewer client."""
    emb.set_provider(ConceptEmbeddings())
    client, viewer = viewer_factory(["JCB"])
    admin = _fake_user("ADMIN")
    app.dependency_overrides[get_current_user] = lambda: admin
    assert client.post("/engine/index/chunks").status_code == 200
    assert client.post("/engine/index/embed").json()["status"] == "ok"
    app.dependency_overrides[get_current_user] = lambda: viewer
    yield client, viewer, admin, corpus
    emb.set_provider(None)


def _saved_rows(db_engine) -> int:
    async def run():
        async with db_engine.connect() as c:
            return (await c.execute(text("select count(*) from saved_articles"))).scalar_one()
    return asyncio.run(run())


def _no_leak(response, *titles: str) -> None:
    body = response.text
    for title in titles:
        assert title not in body, f"leaked {title!r} in {response.status_code} response"


# ---- PUT /articles/{id}/saved ----------------------------------------------------------------------------------------
def test_viewer_cannot_save_an_unauthorized_clients_article(corpus, viewer_factory, db_engine) -> None:
    client, _ = viewer_factory(["JCB"])
    response = client.put(f"/articles/{corpus[ACE_T]}/saved")
    assert response.status_code == 403
    _no_leak(response, ACE_T, "ACE crane chip wafer fab.")
    assert _saved_rows(db_engine) == 0  # nothing was stored
    assert client.get("/articles/saved").json() == []


def test_viewer_without_any_grant_cannot_save_client_articles(corpus, viewer_factory, db_engine) -> None:
    client, _ = viewer_factory(None)
    for title in (ACE_T, JCB_T):
        assert client.put(f"/articles/{corpus[title]}/saved").status_code == 403
    assert _saved_rows(db_engine) == 0


def test_authorized_viewer_can_save_granted_and_unattributed_articles(corpus, viewer_factory) -> None:
    client, _ = viewer_factory(["JCB"])
    ok = client.put(f"/articles/{corpus[JCB_T]}/saved")
    assert ok.status_code == 200 and ok.json()["title"] == JCB_T
    research = client.put(f"/articles/{corpus[RES_T]}/saved")  # not client data: same rule as search
    assert research.status_code == 200
    assert {a["title"] for a in client.get("/articles/saved").json()} == {JCB_T, RES_T}


def test_admin_can_save_any_article(corpus, admin_client) -> None:
    response = admin_client.put(f"/articles/{corpus[ACE_T]}/saved")
    assert response.status_code == 200 and response.json()["title"] == ACE_T


def test_missing_article_is_still_404(corpus, viewer_factory) -> None:
    client, _ = viewer_factory(["JCB"])
    assert client.put(f"/articles/{uuid.uuid4()}/saved").status_code == 404


# ---- GET /articles/saved (defense in depth) --------------------------------------------------------------------------
def test_saved_list_hides_articles_after_access_is_revoked(corpus, viewer_factory, db_engine) -> None:
    client, viewer = viewer_factory(["JCB", "ACE"])
    assert client.put(f"/articles/{corpus[ACE_T]}/saved").status_code == 200
    assert client.put(f"/articles/{corpus[JCB_T]}/saved").status_code == 200

    admin = _fake_user("ADMIN")
    app.dependency_overrides[get_current_user] = lambda: admin
    assert client.put(f"/auth/users/{viewer.id}/clients", json={"clients": ["JCB"]}).status_code == 200
    app.dependency_overrides[get_current_user] = lambda: viewer

    listed = client.get("/articles/saved")
    assert [a["title"] for a in listed.json()] == [JCB_T]
    _no_leak(listed, ACE_T)
    assert _saved_rows(db_engine) == 2  # the record is kept (restored if access is granted again), only hidden
    # re-saving the revoked article is refused too
    assert client.put(f"/articles/{corpus[ACE_T]}/saved").status_code == 403


# ---- GET /search/articles/{id}/similar -------------------------------------------------------------------------------
def test_similar_for_unauthorized_source_is_403_and_runs_no_retrieval(embedded, monkeypatch) -> None:
    client, _viewer, _admin, ids = embedded
    calls: list = []
    real = retrieval.similar_articles

    async def spy(*args, **kwargs):
        calls.append(args)
        return await real(*args, **kwargs)

    monkeypatch.setattr(retrieval, "similar_articles", spy)
    response = client.get(f"/search/articles/{ids[ACE_T]}/similar")
    assert response.status_code == 403
    _no_leak(response, ACE_T, ACE2_T, JCB_T, RES_T, "similarity", "concept-v1")
    assert calls == []  # access is checked before any similarity query


def test_similar_for_authorized_source_filters_neighbours(embedded) -> None:
    client, _viewer, admin, ids = embedded
    response = client.get(f"/search/articles/{ids[JCB_T]}/similar?limit=10")
    assert response.status_code == 200 and response.json()["status"] == "ok"
    titles = {r["title"] for r in response.json()["results"]}
    assert RES_T in titles  # unattributed neighbour is fine
    assert ACE_T not in titles and ACE2_T not in titles
    _no_leak(response, ACE_T, ACE2_T)

    app.dependency_overrides[get_current_user] = lambda: admin
    admin_titles = {r["title"] for r in client.get(f"/search/articles/{ids[JCB_T]}/similar?limit=10").json()["results"]}
    assert {ACE_T, ACE2_T} <= admin_titles  # admin sees every neighbour
    assert client.get(f"/search/articles/{ids[ACE_T]}/similar").status_code == 200


def test_similar_for_missing_article_is_404(embedded) -> None:
    client, *_ = embedded
    assert client.get(f"/search/articles/{uuid.uuid4()}/similar").status_code == 404


def test_viewer_without_grant_gets_403_on_client_articles_and_200_on_unattributed(embedded, viewer_factory) -> None:
    _client, _viewer, _admin, ids = embedded
    client, _ = viewer_factory(None)
    assert client.get(f"/search/articles/{ids[JCB_T]}/similar").status_code == 403
    response = client.get(f"/search/articles/{ids[RES_T]}/similar?limit=10")
    assert response.status_code == 200
    _no_leak(response, ACE_T, ACE2_T, JCB_T)  # only unattributed neighbours may appear
