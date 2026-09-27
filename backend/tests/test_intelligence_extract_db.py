"""POST /intelligence/extract (DB-backed): search -> evidence -> entity context. Test DB only."""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from tests.test_article_search import _seed

BODY_JCB = "Opening. JCB unveiled a new excavator in Pune this week. Closing remarks."


def _set_body(db_engine, article_id: str, raw_text: str) -> None:
    async def run() -> None:
        async with db_engine.begin() as c:
            await c.execute(text("update articles set raw_text=:r where id=:i"), {"r": raw_text, "i": article_id})
    asyncio.run(run())


def _seed_two(db_engine):
    ids = _seed(db_engine, [
        {"title": "JCB launches autonomous excavator", "summary": "robotics on site", "entity": "JCB", "source": "S"},
        {"title": "Excavator demand rises", "summary": "JCB and ISM both mentioned", "entity": "JCB", "source": "S",
         "days_ago": 3},
        {"title": "ISM chips", "summary": "excavator unrelated", "entity": "ISM", "source": "S"},
    ])
    _set_body(db_engine, ids["JCB launches autonomous excavator"], BODY_JCB)
    return ids


def test_end_to_end_response_from_stored_articles(admin_client, db_engine) -> None:
    ids = _seed_two(db_engine)
    r = admin_client.post("/intelligence/extract", json={"query": "excavator", "limit": 10})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "stored_articles" and body["source_result_count"] == 3
    assert {a["article_id"] for a in body["articles"]} == set(ids.values())
    by_key = {e["canonical_key"]: e for e in body["entities"]}
    assert set(by_key) == {"jcb", "ism"}
    jcb = by_key["jcb"]
    assert jcb["entity_type"] == "ORG" and jcb["article_count"] == 2
    assert jcb["article_ids"] == [ids["JCB launches autonomous excavator"], ids["Excavator demand rises"]]
    assert jcb["mentions"] and jcb["evidence"]
    for ev in jcb["evidence"]:
        assert ev["provider"] == "stored_articles" and "jcb" in ev["content_span"].lower()
        assert ev["snippet"] == ev["content_span"]
    assert jcb["evidence"][0]["content_span"] == "JCB launches autonomous excavator"  # first span: title (field order)
    ism = by_key["ism"]                           # ISM has a mention row + verbatim text in its own article
    assert ism["article_ids"] == [ids["ISM chips"]]


def test_evidence_is_verbatim_from_stored_text(admin_client, db_engine) -> None:
    ids = _seed_two(db_engine)
    body = admin_client.post("/intelligence/extract", json={"query": "excavator"}).json()
    stored = {"JCB launches autonomous excavator": ("JCB launches autonomous excavator", "robotics on site", BODY_JCB),
              "Excavator demand rises": ("Excavator demand rises", "JCB and ISM both mentioned", "")}
    urls = {a["url"]: a["title"] for a in body["articles"]}
    for e in body["entities"]:
        for ev in e["evidence"]:
            assert any(ev["content_span"] in part for part in stored.get(urls[ev["source_url"]], ("ISM chips", "excavator unrelated", "")))


def test_entity_without_verbatim_text_support_is_dropped(admin_client, db_engine) -> None:
    ids = _seed(db_engine, [{"title": "Excavator news", "summary": "nothing about clients here", "entity": "JCB",
                             "source": "S"}])                      # mention row exists, text never says JCB
    body = admin_client.post("/intelligence/extract", json={"query": "excavator"}).json()
    assert body["source_result_count"] == 1 and body["entities"] == []


def test_deterministic_and_search_order_preserved(admin_client, db_engine) -> None:
    _seed_two(db_engine)
    a = admin_client.post("/intelligence/extract", json={"query": "excavator"}).json()
    assert a == admin_client.post("/intelligence/extract", json={"query": "excavator"}).json()
    plain = admin_client.get("/search/articles", params={"q": "excavator"}).json()
    assert [x["article_id"] for x in a["articles"]] == [x["article_id"] for x in plain["results"]]  # ranking untouched


def test_no_results_and_validation(admin_client, db_engine) -> None:
    _seed_two(db_engine)
    body = admin_client.post("/intelligence/extract", json={"query": "zzzunmatched"}).json()
    assert body["source_result_count"] == 0 and body["entities"] == [] and body["articles"] == []
    assert admin_client.post("/intelligence/extract", json={"query": ""}).status_code == 422
    assert admin_client.post("/intelligence/extract", json={"query": "x", "limit": 99}).status_code == 422


def test_admin_only(viewer_client) -> None:
    assert viewer_client.post("/intelligence/extract", json={"query": "excavator"}).status_code == 403


def test_anonymous_rejected(anon_client) -> None:
    assert anon_client.post("/intelligence/extract", json={"query": "excavator"}).status_code in (401, 403)
