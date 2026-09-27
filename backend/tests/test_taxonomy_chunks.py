"""Multi-label taxonomy (rules first), chunks with offsets, label/person/technology filters, pipeline indexing."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.adapters.ingestion.base import NormalizedDocument
from app.models.content import Article
from app.services import profiles as prof
from app.services import registry_ingestion as reg
from app.services.chunking import make_chunks
from app.services.taxonomy import compute_labels
from tests.test_article_search import _seed


def _article(title, summary="", entity="JCB", score=0.6):
    return Article(url="https://x.test/a", title=title, summary=summary,
                   score_reason=json.dumps({"decision": "accepted", "entity": entity, "matched_terms": [entity], "relevance_score": score,
                                            "source_type": "news"}))


def _labels(article, **kw):
    profiles = {n: prof.code_profile(n) for n in ("JCB", "ACE", "Definedge", "ISM", "VIPS", "Vikas")}
    for k, v in kw.pop("profile_updates", {}).items():
        profiles["JCB"] = profiles["JCB"].model_copy(update={k: v})
    return {row["label"]: row for row in compute_labels(article, tier=kw.get("tier", "TIER_2"), source_class=kw.get("source_class", "news"),
                                                         profiles=profiles, clients=kw.get("clients", ["JCB"]))}


def test_a_headline_carries_several_labels_never_forced_into_one() -> None:
    labels = _labels(_article("JCB India launches new excavator in partnership with Bosch, shares rise after quarter results"))
    assert {"company", "product_business_movement", "partnership", "market_financial_signal"} <= set(labels)
    assert labels["company"]["evidence"]["basis"] == "stored relevance decision"
    assert all(0 <= r["confidence"] <= 1 and r["evidence"]["terms"] for r in labels.values())


def test_unlabelled_items_stay_unlabelled_and_no_label_without_evidence() -> None:
    labels = _labels(_article("Quiet Tuesday at the plant", entity="JCB", score=0.5))
    assert set(labels) == {"company"}  # accepted for the client, but nothing else is stated


def test_competitor_and_technology_need_profile_or_theme_evidence() -> None:
    a = _article("JCB rival Caterpillar tests humanoid robots and digital twins for excavators")
    assert "competitor" not in _labels(a)
    with_profile = _labels(a, profile_updates={"competitors": ["Caterpillar"]})
    assert with_profile["competitor"]["evidence"]["terms"] == ["Caterpillar"]
    assert {"Robotics", "Humanoids", "Digital twins"} <= set(with_profile["technology"]["evidence"]["terms"])


def test_opportunity_is_only_a_detected_signal_never_confirmed() -> None:
    labels = _labels(_article("Startup raises funding round to expand new plant"))
    assert labels["opportunity"]["evidence"]["confirmed"] is False and {"funding", "expansion"} <= set(labels["opportunity"]["evidence"]["signals"])
    assert labels["opportunity"]["confidence"] <= 0.9


def test_source_tier_lowers_label_confidence() -> None:
    a = _article("JCB launches new excavator")
    assert _labels(a, tier="TIER_1")["product_business_movement"]["confidence"] > _labels(a, tier="TIER_4")["product_business_movement"]["confidence"]


def test_research_label_from_source_class_or_markers() -> None:
    assert "research" in _labels(_article("A new arXiv preprint on legged locomotion"))
    assert _labels(_article("Model card", entity="ISM"), source_class="research")["research"]["evidence"]["basis"] == "research source class"


def test_ai_model_dictionary_is_literal() -> None:
    assert "ai_model" in _labels(_article("Startup fine-tunes Llama for construction safety"))
    assert "ai_model" not in _labels(_article("Excavator sales rise"))


def test_chunks_have_exact_offsets_and_overlap() -> None:
    body = " ".join(f"word{i}" for i in range(1000)) + "  tail"
    spans = make_chunks("raw_text", body)
    assert len(spans) >= 4 and all(body[s.start:s.end] == s.text for s in spans)
    assert spans[0].start == 0 and spans[-1].end == len(body)
    assert set(spans[0].text.split()) & set(spans[1].text.split())  # overlap
    assert len(spans[0].text.split()) == 250
    assert [s.index for s in spans] == list(range(len(spans)))
    assert make_chunks("title", "  JCB launches  ")[0].text == "JCB launches" and make_chunks("summary", "  ") == []
    assert len(make_chunks("raw_text", "short body of a few words")) == 1


def _docs(source_id):
    now = datetime.now(timezone.utc)
    t = "JCB India expands Pune plant with new loader line in partnership with Bosch"
    return [NormalizedDocument(source_id=source_id, canonical_url="https://trade.example.org/a/1", title=t,
                               published_at=now - timedelta(days=1), retrieved_at=now, raw_content=t + ". Details follow.",
                               normalized_content=t + ". Details follow.", content_type="text/html", metadata={})]


def test_pipeline_persists_chunks_and_labels_for_accepted_articles(admin_client, db_engine, monkeypatch) -> None:
    async def fake_fetch(spec, source, retrieved_at):
        return _docs(source.id), []
    monkeypatch.setattr(reg, "_fetch", fake_fetch)
    src = admin_client.post("/sources", json={"name": "Trade", "url": "https://trade.example.org/feed", "source_type": "RSS", "tier": "TIER_2",
                                              "config": {"fetch_mode": "rss"}}).json()
    assert admin_client.post(f"/sources/{src['id']}/fetch").status_code == 200

    async def rows():
        async with db_engine.connect() as c:
            labels = (await c.execute(text("select label, method from article_labels order by label"))).all()
            chunks = (await c.execute(text("select field, char_start, char_end, text from document_chunks order by field"))).all()
            return labels, chunks
    labels, chunks = asyncio.run(rows())
    assert {"company", "partnership", "product_business_movement"} <= {l for l, _m in labels} and {m for _l, m in labels} == {"rule"}
    assert {c[0] for c in chunks} == {"title", "summary"}  # body identical to the summary is not chunked twice
    assert all(c[2] > c[1] and c[3] for c in chunks)


def test_search_filters_label_person_and_technology(admin_client, db_engine) -> None:
    ids = _seed(db_engine, [
        {"title": "JCB launches digital twin for excavators", "summary": "", "entity": "JCB", "source": "S1"},
        {"title": "JCB reports quarterly results", "summary": "", "entity": "JCB", "source": "S2"},
    ])

    async def label(article_id, name):
        async with db_engine.begin() as c:
            await c.execute(text("insert into article_labels (id, article_id, label, method, confidence) values (gen_random_uuid(), :a, :l, 'rule', 0.5)"),
                            {"a": article_id, "l": name})
            await c.execute(text("update articles set author = 'Jane Doe' where id = :a"), {"a": article_id})
    asyncio.run(label(ids["JCB launches digital twin for excavators"], "technology"))
    hits = admin_client.get("/search/articles?q=JCB&label=technology").json()
    assert [h["title"] for h in hits["results"]] == ["JCB launches digital twin for excavators"]
    assert admin_client.get("/search/articles?q=JCB&label=bogus").status_code == 422
    assert admin_client.get("/search/articles?q=JCB&person=jane").json()["count"] == 1
    assert admin_client.get("/search/articles?q=JCB&technology=digital%20twin").json()["count"] == 1
    assert admin_client.get("/search/articles?q=JCB&technology=blockchain").json()["count"] == 0


def test_architecture_trace_audit_passes_on_pipeline_output_and_catches_tampering(admin_client, db_engine, monkeypatch) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.services.trace_audit import audit_architecture

    async def fake_fetch(spec, source, retrieved_at):
        return _docs(source.id), []
    monkeypatch.setattr(reg, "_fetch", fake_fetch)
    src = admin_client.post("/sources", json={"name": "Trade", "url": "https://trade.example.org/feed", "source_type": "RSS", "tier": "TIER_2",
                                              "config": {"fetch_mode": "rss"}}).json()
    admin_client.post(f"/sources/{src['id']}/fetch")

    async def audit():
        async with AsyncSession(db_engine) as s:
            return await audit_architecture(s)
    clean = asyncio.run(audit())
    assert clean["violations"] == [] and clean["evidence"] > 0

    async def tamper():
        async with db_engine.begin() as c:
            await c.execute(text("update document_chunks set char_end = char_end - 1 where field = 'title'"))
            await c.execute(text("update articles set canonical_url = 'https://news.google.com/rss/articles/x'"))
    asyncio.run(tamper())
    kinds = " | ".join(asyncio.run(audit())["violations"])
    assert "offsets do not reproduce" in kinds and "no canonical publisher URL" in kinds


def test_article_detail_shows_provenance_decision_labels_and_never_the_discovery_url(admin_client, db_engine) -> None:
    ids = _seed(db_engine, [
        {"title": "JCB launches digital twin", "summary": "JCB launches a digital twin.", "entity": "JCB", "source": "S1", "canonical_url": "https://pub.test/a"},
        {"title": "JCB wrapped", "summary": "", "entity": "JCB", "source": "S2", "url": "https://news.google.com/rss/articles/abc"},
    ])
    admin_client.post("/engine/index/chunks")
    admin_client.post("/engine/index/labels")
    d = admin_client.get(f"/articles/{ids['JCB launches digital twin']}/detail").json()
    assert d["url"] == "https://pub.test/a" and d["source"]["tier"] == "TIER_2" and d["decision"]["decision"] == "accepted" and d["clients"] == ["JCB"]
    assert {"company", "technology"} <= {l["label"] for l in d["labels"]} and d["chunks"] and d["status"] == "accepted"
    w = admin_client.get(f"/articles/{ids['JCB wrapped']}/detail").json()
    assert w["url"] is None and "No publisher URL" in w["url_note"] and "news.google.com" not in str(w)
    assert admin_client.get("/articles/00000000-0000-0000-0000-000000000000/detail").status_code == 404
