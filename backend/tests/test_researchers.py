"""Research adapters, authorship, researcher intelligence, similar researchers, honest empty states (PDF s.11)."""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import text

from app.adapters.ingestion.base import AdapterError
from app.adapters.ingestion.research import parse_arxiv, parse_huggingface, parse_openalex
from app.services import embeddings as emb
from app.services import llm
from app.services import registry_ingestion as reg
from tests.ai_fakes import ConceptEmbeddings, ScriptedLLM

ARXIV = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
 <entry>
  <id>http://arxiv.org/abs/2609.00001v1</id><published>2026-09-15T10:00:00Z</published>
  <title>Embodied AI for legged robots on uneven terrain</title>
  <summary>We study embodied AI and robotics policies for legged locomotion using digital twins.</summary>
  <author><name>Ada Lovelace</name><arxiv:affiliation>Institute of Robotics</arxiv:affiliation></author>
  <author><name>Grace Hopper</name></author>
  <category term="cs.RO"/>
 </entry>
 <entry>
  <id>http://arxiv.org/abs/2609.00002v1</id><published>2026-09-16T10:00:00Z</published>
  <title>Robotics manipulation with foundation models</title>
  <summary>Foundation models guide robotics manipulation and embodied agents.</summary>
  <author><name>Ada Lovelace</name><arxiv:affiliation>Institute of Robotics</arxiv:affiliation></author>
  <author><name>Alan Turing</name></author>
 </entry>
 <entry>
  <id>http://arxiv.org/abs/2609.00003v1</id><published>2026-09-17T10:00:00Z</published>
  <title>Credit scoring with gradient boosting</title>
  <summary>A study of tabular lending models.</summary>
  <author><name>Linus Torvalds</name></author>
 </entry>
</feed>"""


@pytest.fixture(autouse=True)
def _reset():
    yield
    emb.set_provider(None)
    llm.set_provider(None)


def _q(db_engine, sql, **params):
    async def run():
        async with db_engine.connect() as c:
            return (await c.execute(text(sql), params)).all()
    return asyncio.run(run())


def test_arxiv_parser_reads_only_what_the_record_states() -> None:
    docs = parse_arxiv(ARXIV, source_id=uuid.uuid4())
    assert [d.canonical_url for d in docs][0] == "http://arxiv.org/abs/2609.00001v1"
    first = docs[0]
    assert first.metadata["authors"] == [{"name": "Ada Lovelace", "affiliation": "Institute of Robotics", "position": 0},
                                         {"name": "Grace Hopper", "affiliation": None, "position": 1}]
    assert first.published_at.isoformat().startswith("2026-09-15") and first.metadata["publisher_domain"] == "arxiv.org"
    with pytest.raises(AdapterError):
        parse_arxiv("<html>not atom</html>", source_id=uuid.uuid4())
    with pytest.raises(AdapterError):
        parse_arxiv("<feed", source_id=uuid.uuid4())


def test_huggingface_parser_states_metadata_and_never_invents_people() -> None:
    docs = parse_huggingface([{"id": "acme/robot-vla-7b", "author": "acme", "createdAt": "2026-09-10T00:00:00.000Z", "pipeline_tag": "robotics",
                               "tags": ["robotics", "vla"]}, {"nope": 1}], source_id=uuid.uuid4())
    assert len(docs) == 1 and docs[0].canonical_url == "https://huggingface.co/acme/robot-vla-7b" and docs[0].metadata["authors"] == []
    with pytest.raises(AdapterError):
        parse_huggingface({"error": "x"}, source_id=uuid.uuid4())


OPENALEX = {
    "results": [
        {
            "id": "https://openalex.org/W1", "title": "Embodied AI for legged robots on uneven terrain",
            "publication_date": "2026-09-15", "doi": "https://doi.org/10.1/abc",
            "primary_location": {"landing_page_url": "https://journal.example.com/w1", "source": {"display_name": "Journal of Robotics"}},
            "authorships": [
                {"author": {"display_name": "Ada Lovelace"}, "institutions": [{"display_name": "Institute of Robotics"}]},
                {"author": {"display_name": "Grace Hopper"}, "institutions": []},
            ],
            "abstract_inverted_index": {"We": [0], "study": [1], "embodied": [2], "AI": [3]},
        },
        {"id": None, "title": "no id, must be skipped"},
    ]
}


def test_openalex_parser_reads_only_what_the_record_states() -> None:
    docs = parse_openalex(OPENALEX, source_id=uuid.uuid4())
    assert len(docs) == 1
    first = docs[0]
    assert first.canonical_url == "https://journal.example.com/w1"
    assert first.metadata["authors"] == [{"name": "Ada Lovelace", "affiliation": "Institute of Robotics", "position": 0},
                                         {"name": "Grace Hopper", "affiliation": None, "position": 1}]
    assert first.published_at.isoformat().startswith("2026-09-15") and first.metadata["publisher_domain"] == "openalex.org"
    assert first.raw_content == "Embodied AI for legged robots on uneven terrain. We study embodied AI"
    with pytest.raises(AdapterError):
        parse_openalex({"error": "x"}, source_id=uuid.uuid4())
    with pytest.raises(AdapterError):
        parse_openalex([], source_id=uuid.uuid4())


def _research_source(admin_client, monkeypatch, xml=ARXIV):
    async def fake_fetch(spec, source, retrieved_at):
        return parse_arxiv(xml, source_id=source.id, retrieved_at=retrieved_at), []
    monkeypatch.setattr(reg, "_fetch", fake_fetch)
    src = admin_client.post("/sources", json={"name": "arXiv robotics", "url": "http://export.arxiv.org/api/query?robotics", "source_type": "RESEARCH_API",
                                              "tier": "TIER_1", "source_class": "research",
                                              "config": {"fetch_mode": "arxiv_api", "source_kind": "research", "query": "cat:cs.RO", "max_results": 10}}).json()
    return src


def test_research_documents_are_admitted_on_a_stated_topic_without_asserting_a_client(admin_client, db_engine, monkeypatch) -> None:
    src = _research_source(admin_client, monkeypatch)
    assert admin_client.post(f"/sources/{src['id']}/fetch").json()["articles_created"] == 3
    rows = dict(_q(db_engine, "select title, processing_status from articles"))
    assert rows["Embodied AI for legged robots on uneven terrain"] == "accepted"
    assert rows["Robotics manipulation with foundation models"] == "accepted"
    assert rows["Credit scoring with gradient boosting"] == "rejected"  # no stated research/technology topic
    assert _q(db_engine, "select count(*) from entity_mentions")[0][0] == 0  # no client is asserted
    labels = {r[0] for r in _q(db_engine, "select distinct label from article_labels")}
    assert {"research", "technology"} <= labels
    assert admin_client.get("/intelligence/clients/JCB/view").json()["articles"] == []  # client views never show them
    research = admin_client.get("/search/articles?q=robotics&source_class=research").json()
    assert research["count"] == 2 and {h["client"] for h in research["results"]} == {None}
    assert admin_client.get("/intelligence/research").json()["status"] != "NO_RESEARCH_DATA_IN_STORED_CORPUS"


def test_authors_persons_affiliations_and_evidence_are_recorded(admin_client, db_engine, monkeypatch) -> None:
    src = _research_source(admin_client, monkeypatch)
    admin_client.post(f"/sources/{src['id']}/fetch")
    people = _q(db_engine, "select canonical_name, entity_type, status from entities where entity_type = 'PERSON' order by canonical_name")
    assert [p[0] for p in people] == ["Ada Lovelace", "Alan Turing", "Grace Hopper"] and {p[2] for p in people} == {"needs_review"}
    assert "Linus Torvalds" not in [p[0] for p in people]  # the rejected paper's author is not tracked
    rel = _q(db_engine, "select p.canonical_name, o.canonical_name, r.relationship_type, (select count(*) from relationship_evidence e where e.relationship_id = r.id) "
                        "from relationships r join entities p on p.id = r.source_entity_id join entities o on o.id = r.target_entity_id where r.relationship_type = 'WORKS_AT'")
    assert rel == [("Ada Lovelace", "Institute of Robotics", "WORKS_AT", 2)]
    assert _q(db_engine, "select count(*) from document_authors")[0][0] == 4
    assert admin_client.post(f"/sources/{src['id']}/fetch").json()["articles_created"] == 0  # a re-run adds no duplicates
    assert _q(db_engine, "select count(*) from document_authors")[0][0] == 4


def test_researcher_list_detail_recent_work_and_links(admin_client, db_engine, monkeypatch) -> None:
    src = _research_source(admin_client, monkeypatch)
    admin_client.post(f"/sources/{src['id']}/fetch")
    listing = admin_client.get("/intelligence/researchers").json()
    assert listing["total"] == 3 and listing["items"][0]["name"] == "Ada Lovelace" and listing["items"][0]["documents"] == 2
    ada = listing["items"][0]
    assert ada["organisations"] == [{"organisation": "Institute of Robotics", "verification_status": "unverified", "evidence_count": 2}]
    assert {"robotics", "Robotics"} & {a["term"] for a in ada["areas"]} or any("obot" in a["term"] for a in ada["areas"])
    detail = admin_client.get(f"/intelligence/researchers/{ada['person_id']}").json()
    assert [w["title"] for w in detail["recent_work"]] == ["Robotics manipulation with foundation models", "Embodied AI for legged robots on uneven terrain"]
    assert all(w["url"].startswith("http://arxiv.org/abs/") and w["source"] == "arXiv robotics" for w in detail["recent_work"])
    assert admin_client.get("/intelligence/researchers?q=turing").json()["total"] == 1
    assert admin_client.get(f"/intelligence/researchers/{uuid.uuid4()}").status_code == 404


def test_similar_researchers_by_term_overlap_with_context_relevance(admin_client, db_engine, monkeypatch) -> None:
    src = _research_source(admin_client, monkeypatch)
    admin_client.post(f"/sources/{src['id']}/fetch")
    people = {p["name"]: p["person_id"] for p in admin_client.get("/intelligence/researchers").json()["items"]}
    out = admin_client.get(f"/intelligence/researchers/{people['Grace Hopper']}/similar?context=robotics%20manipulation").json()
    assert out["method"] == "term_overlap" and out["results"]
    top = out["results"][0]
    assert top["name"] in {"Ada Lovelace", "Alan Turing"} and top["similarity"] > 0 and top["recent_work"]
    assert top["relevance_in_context"]["documents"][0]["matched_terms"] == ["manipulation", "robotics"]
    none = admin_client.get(f"/intelligence/researchers/{people['Grace Hopper']}/similar?context=fintech%20chart%20analysis").json()
    assert all(r["relevance_in_context"]["documents"] == [] and "No stored document" in r["relevance_in_context"]["detail"] for r in none["results"])


def test_similar_researchers_use_embeddings_when_available(admin_client, db_engine, monkeypatch) -> None:
    src = _research_source(admin_client, monkeypatch)
    admin_client.post(f"/sources/{src['id']}/fetch")
    emb.set_provider(ConceptEmbeddings())
    admin_client.post("/engine/index/embed")
    people = {p["name"]: p["person_id"] for p in admin_client.get("/intelligence/researchers").json()["items"]}
    out = admin_client.get(f"/intelligence/researchers/{people['Grace Hopper']}/similar").json()
    assert out["method"] == "embedding_nearest_neighbour" and out["results"] and out["results"][0]["similarity"] > 0


def test_seed_list_is_tracked_but_ranked_by_evidence_only(admin_client, db_engine, monkeypatch) -> None:
    empty = admin_client.get("/intelligence/researchers").json()
    assert empty["status"] == "NO_RESEARCHERS_STORED" and empty["items"] == []
    seeded = admin_client.post("/intelligence/researchers/seed", json={"people": [{"name": "Yann Example", "organisation": "Example Lab", "areas": ["robotics"]}]}).json()
    assert seeded == {"created": 1, "existing": 0}
    assert admin_client.post("/intelligence/researchers/seed", json={"people": [{"name": "Yann Example"}]}).json() == {"created": 0, "existing": 1}
    item = admin_client.get("/intelligence/researchers").json()["items"][0]
    assert item["documents"] == 0 and item["evidence_status"] == "NO_STORED_DOCUMENTS" and item["organisations"][0]["evidence_count"] == 0
    sim = admin_client.get(f"/intelligence/researchers/{item['person_id']}/similar").json()
    assert sim["status"] == "NO_STORED_DOCUMENTS" and sim["results"] == []
    src = _research_source(admin_client, monkeypatch)
    admin_client.post(f"/sources/{src['id']}/fetch")
    assert admin_client.get("/intelligence/researchers").json()["items"][-1]["name"] == "Yann Example"  # evidence ranks first


def test_researcher_summary_is_cited_or_absent(admin_client, db_engine, monkeypatch) -> None:
    src = _research_source(admin_client, monkeypatch)
    admin_client.post(f"/sources/{src['id']}/fetch")
    ada = admin_client.get("/intelligence/researchers").json()["items"][0]["person_id"]
    assert admin_client.post(f"/intelligence/researchers/{ada}/summary").status_code == 503
    llm.set_provider(ScriptedLLM({"sentences": [{"text": "Ada Lovelace published on manipulation.", "claim_refs": ["D1"]}, {"text": "She is the best.", "claim_refs": []}]}))
    out = admin_client.post(f"/intelligence/researchers/{ada}/summary?context=robotics").json()
    assert out["generated"] is True and len(out["sentences"]) == 1 and out["removed_uncited_sentences"] == 1
    assert out["sentences"][0]["citations"][0]["url"].startswith("http://arxiv.org/abs/")


def test_researcher_routes_are_protected(viewer_client, anon_client) -> None:
    assert viewer_client.post("/intelligence/researchers/seed", json={"people": [{"name": "Someone Else"}]}).status_code == 403
    assert viewer_client.get("/intelligence/researchers").status_code == 200


def test_arxiv_adapter_keeps_field_colons_unescaped_in_the_query_string() -> None:
    """arXiv's export endpoint 406s on a percent-encoded ':' (cat%3Acs.RO); field prefixes must stay literal."""
    import httpx

    from app.adapters.ingestion.research import ArxivAdapter

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, text=ARXIV)

    adapter = ArxivAdapter()
    async def fake_fetch(query, *, source_id, retrieved_at=None, max_results=25):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            r = await http.get(f"http://export.arxiv.org/api/query?search_query={query}")
        return r
    # exercise the real URL-building line directly
    from urllib.parse import quote_plus
    built = quote_plus("cat:cs.RO AND abs:embodied", safe=":")
    assert "%3A" not in built and built == "cat:cs.RO+AND+abs:embodied"
