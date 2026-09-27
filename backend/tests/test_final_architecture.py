"""Final architecture pass: event hardening, insight limitations, pipeline status, research interface, pagination,
a full scheduler cycle on the test DB, stale claim evidence, evidence carriers in the trace audit, and API security.
Test database only; no network."""
from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.ingestion.base import NormalizedDocument
from app.config.scheduler import SCHEDULER_CONFIG
from app.main import app
from app.services import background_scheduler as bs
from app.services import registry_ingestion as ri
from app.services.article_evidence import search_with_evidence
from app.services.client_engine import client_briefing
from app.services.stored_events import extract_events
from app.services.trace_audit import audit_briefing, audit_evidence_items, audit_search_evidence
from tests.test_article_search import _seed
from tests.test_claims import _payload as claim_payload
from tests.test_claims import _setup as claim_setup
from tests.test_client_intelligence import _corpus


def _get(client, path, **params):
    r = client.get(path, params=params)
    assert r.status_code == 200, r.text
    return r.json()


# ---- events ------------------------------------------------------------------------------------------------------------

def test_planned_or_future_sentences_are_not_stated_events() -> None:
    jcb = {"jcb": {"JCB"}}
    for sentence in ("JCB will launch a new backhoe next month.", "JCB plans to launch a loader.",
                     "JCB is expected to unveil a machine.", "JCB has scheduled the launch of a crane."):
        assert extract_events("a", {"raw_text": sentence}, jcb) == [], sentence
    assert [e.event_type for e in extract_events("a", {"raw_text": "JCB launched a new backhoe."}, jcb)] == ["launch"]


def test_events_expose_id_type_entities_articles_sources_evidence_and_time_source(admin_client, db_engine) -> None:
    _corpus(db_engine)
    v = _get(admin_client, "/intelligence/clients/JCB/view")
    assert v["events"]
    for e in v["events"]:
        assert e["event_id"] == e["event_key"] and e["event_key"].startswith("ev-") and e["event_type"] and e["title"]
        assert e["entity_keys"] == ["jcb"] and e["article_ids"] and e["source_count"] >= 1 and e["evidence"]
        assert e["event_time_source"] in {"article_text", "unknown"} and (e["event_time"] is None) == (e["event_time_source"] == "unknown")
    tl = {t["event_key"]: t for t in v["timeline"]}
    assert all(tl[e["event_key"]]["ordering_time_source"] in {"event_time", "publication_fallback", "discovery_fallback"} for e in v["events"])


def test_insights_state_rule_numbers_sources_evidence_strength_and_limitations(admin_client, db_engine) -> None:
    _corpus(db_engine)
    for i in _get(admin_client, "/intelligence/clients/JCB/insights"):
        assert i["rule_id"] and i["basis"] and i["event_keys"] and i["evidence"] and 0 <= i["strength"] <= 1
        assert any("not mean verified" in x for x in i["limitations"]) and any("stored corpus" in x for x in i["limitations"])
    cluster = next(i for i in _get(admin_client, "/intelligence/clients/JCB/insights") if i["rule_id"] == "I2_activity_cluster")
    assert any("de-duplication" in x for x in cluster["limitations"])


# ---- domain views are labelled as keyword evidence, not classification ------------------------------------------------

def test_technology_and_industry_label_literal_matching(admin_client, db_engine) -> None:
    _seed(db_engine, [{"title": "JCB tests robots and excavator lines", "entity": "JCB", "source": "S", "url": "https://lm.test/1"}])
    tech, ind = _get(admin_client, "/intelligence/technology"), _get(admin_client, "/intelligence/industry")
    assert "not a semantic classification" in tech["method"] and "not a semantic classification" in ind["method"]
    sigs = [s for t in tech["themes"] for s in t["signals"]] + [s for c in ind["clients"] for t in c["industry_terms"] for s in t["signals"]]
    assert sigs and all(s["match_type"] == "literal_term_match" for s in sigs)


# ---- research interface ------------------------------------------------------------------------------------------------

def test_research_is_explicitly_empty_and_never_client_news(admin_client, db_engine) -> None:
    _seed(db_engine, [{"title": "JCB launches backhoe", "entity": "JCB", "source": "S", "url": "https://rs1.test/1"}])
    v = _get(admin_client, "/intelligence/research")
    assert v["status"] == "NO_RESEARCH_DATA_IN_STORED_CORPUS" and v["records"] == []
    assert "not research intelligence" in v["reason"] and "authors" in v["interface"]["stored_when_the_source_states_them"] and "DOI or paper ID" in v["interface"]["not_yet_stored"]


def test_research_records_appear_only_for_research_class_sources(admin_client, db_engine) -> None:
    ids = _seed(db_engine, [{"title": "A study of humanoid grasping", "entity": "JCB", "source": "S", "url": "https://arxiv.test/abs/1"},
                            {"title": "JCB news", "entity": "JCB", "source": "S", "url": "https://news.test/2"}])

    async def mark():
        async with db_engine.begin() as c:
            await c.execute(text("update articles set score_reason = jsonb_set(score_reason::jsonb, '{source_type}', '\"research\"')::text where id=:i"),
                            {"i": ids["A study of humanoid grasping"]})
    asyncio.run(mark())
    v = _get(admin_client, "/intelligence/research")
    assert v["status"] == "RESEARCH_RECORDS_FOUND" and [r["title"] for r in v["records"]] == ["A study of humanoid grasping"]
    r = v["records"][0]
    assert r["url"] == "https://arxiv.test/abs/1" and r["authors"] == [] and r["authors_status"] == "NOT_STORED"
    assert r["evidence"]["quote"] == "A study of humanoid grasping"


# ---- pagination (client-scoped "show all N") ---------------------------------------------------------------------------

def test_articles_offset_pages_are_disjoint_ordered_and_complete(admin_client, db_engine) -> None:
    _seed(db_engine, [{"title": f"JCB page item {i}", "entity": "JCB", "source": "S", "days_ago": i, "url": f"https://pg{i}.test/x"} for i in range(5)])
    full = [a["id"] for a in _get(admin_client, "/articles", entity_key="jcb", limit=500)]
    pages = [[a["id"] for a in _get(admin_client, "/articles", entity_key="jcb", limit=2, offset=o)] for o in (0, 2, 4, 6)]
    assert [len(p) for p in pages] == [2, 2, 1, 0] and sum(pages, []) == full and len(set(full)) == 5
    assert admin_client.get("/articles", params={"offset": -1}).status_code == 422


# ---- pipeline status ---------------------------------------------------------------------------------------------------

def test_pipeline_status_exposes_counts_sources_and_cycle_fields(admin_client, db_engine) -> None:
    _seed(db_engine, [{"title": "JCB one", "entity": "JCB", "source": "S", "url": "https://ps1.test/1"},
                      {"title": "rej", "entity": "JCB", "source": "S", "status": "rejected", "gate": False}])
    v = _get(admin_client, "/intelligence/pipeline/status")
    assert (v["accepted_total"], v["rejected_total"], v["sources_total"], v["sources_active"]) == (1, 1, 1, 1)
    assert v["last_cycle"] is None and v["next_cycle_at"] is None and v["scheduler"]["running"] is False
    assert {"latest_article_ingested_at", "continuous_updates", "clients", "health_checks_recorded"} <= set(v)


def test_scheduler_records_next_cycle_and_bounds_public_errors(monkeypatch) -> None:
    runner = bs.SchedulerRunner()

    async def fake_run():
        return {"total_accepted": 1, "failed_sources": ["x" * 900] * 30}

    async def fake_sleep(seconds):
        runner.running = False

    monkeypatch.setattr(bs, "run_scheduled_pipeline", fake_run)
    monkeypatch.setattr(bs.asyncio, "sleep", fake_sleep)
    runner.running = True
    asyncio.run(runner._scheduler_loop())
    runner.running = True
    st = runner.status()
    assert st["next_run_at"] and len(st["last_summary"]["failed_sources"]) == 20 and all(len(x) <= 200 for x in st["last_summary"]["failed_sources"])


# ---- one full scheduler cycle on the test DB, twice --------------------------------------------------------------------

def test_scheduler_cycle_ingests_dedupes_isolates_failures_and_records_health(admin_client, db_engine, monkeypatch) -> None:
    maker = async_sessionmaker(db_engine, expire_on_commit=False)
    specs = ri.google_specs()
    jcb_query = next(q for c, q, u in specs if c == "JCB")
    ace_query = next(q for c, q, u in specs if c == "ACE")
    now = datetime.now(timezone.utc)

    async def fake_fetch(self, **kw):
        if kw["query"] == ace_query:
            raise RuntimeError("feed down")
        if kw["query"] != jcb_query:
            return []
        return [NormalizedDocument(
            source_id=uuid.UUID(kw["source_id"]), canonical_url=f"https://autocarpro.in/news/{n}", title=title,
            published_at=now - timedelta(hours=2), retrieved_at=now, raw_content=title, normalized_content=title,
            content_type="text/html", metadata={"publisher_domain": "autocarpro.in"})
            for n, title in enumerate(("JCB India expands manufacturing capacity at Pune plant",
                                       "JCB India unveils new backhoe loader range for construction customers"))]

    original = ri.run_google_query

    async def no_media(maker_, client, query, url, **kw):
        kw["resolve_media"] = False                                    # no network image lookups in tests
        return await original(maker_, client, query, url, **kw)

    monkeypatch.setattr(ri.GoogleNewsAdapter, "fetch", fake_fetch)
    monkeypatch.setattr(bs, "run_google_query", no_media)
    monkeypatch.setattr(bs, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(SCHEDULER_CONFIG, "registry_enabled", False)
    monkeypatch.setattr(SCHEDULER_CONFIG, "theme_discovery_enabled", False)      # covered separately in test_closure

    async def counts():
        async with db_engine.connect() as c:
            arts = (await c.execute(text("select count(*), count(*) filter (where processing_status='accepted'), "
                                         "count(*) filter (where canonical_url like '%news.google.com%') from articles"))).one()
            health = (await c.execute(text("select h.status, count(*) from source_health h group by 1 order by 1"))).all()
            return arts, dict(health)

    first = asyncio.run(bs.run_scheduled_pipeline())
    arts1, health1 = asyncio.run(counts())
    assert arts1[0] >= 1 and arts1[1] >= 1 and arts1[2] == 0                          # persisted, gated, no wrapper URLs
    assert first["by_client"]["JCB"]["total_fetched"] == 2 and any("feed down" in f for f in first["failed_sources"])
    assert health1["error"] == 1 and health1["ok"] == len(specs) - 1                   # every attempt measured, failure isolated
    second = asyncio.run(bs.run_scheduled_pipeline())
    arts2, health2 = asyncio.run(counts())
    assert arts2 == arts1 and second["total_accepted"] == 0                            # repeated cycle: no duplicates
    assert health2["error"] == 2 and health2["ok"] == 2 * (len(specs) - 1)


# ---- claims: stale evidence --------------------------------------------------------------------------------------------

def test_claim_whose_evidence_is_no_longer_in_the_corpus_is_rejected(admin_client, db_engine) -> None:
    body, event = claim_setup(admin_client, db_engine)
    payload = claim_payload(body, event)
    assert admin_client.post("/claims", json=payload).status_code == 201

    async def wipe():
        async with db_engine.begin() as c:
            await c.execute(text("delete from articles"))
    asyncio.run(wipe())
    stale = admin_client.post("/claims", json=payload)
    assert stale.status_code == 422


# ---- trace audit covers every evidence carrier --------------------------------------------------------------------------

def _run(db_engine, coro_fn):
    async def go():
        async with AsyncSession(db_engine) as s:
            return await coro_fn(s)
    return asyncio.run(go())


def test_audit_search_evidence_and_briefing_pass_and_catch_tampering(admin_client, db_engine) -> None:
    _corpus(db_engine)

    async def search_ok(s):
        found = await search_with_evidence(s, "JCB launches", limit=10)
        clean = await audit_search_evidence(s, found.results)
        found.results[0].spans[0].start += 1                                            # offsets no longer reproduce the quote
        found.results[1].url = "https://news.google.com/rss/articles/X"                # wrapper as provenance
        return clean, await audit_search_evidence(s, found.results)

    clean, dirty = _run(db_engine, search_ok)
    assert clean["violations"] == [] and clean["evidence"] > 0
    text_ = " | ".join(dirty["violations"])
    assert "offsets do not reproduce" in text_ and "not the canonical publisher URL" in text_

    async def brief(s):
        b = await client_briefing(s, "JCB", limit=500)
        good = await audit_briefing(s, b)
        b.key_events[0].evidence[0].content_span = "JCB acquired the moon."
        return good, await audit_briefing(s, b)

    good, bad = _run(db_engine, brief)
    assert good["violations"] == [] and good["evidence"] > 0 and any("span not found" in v for v in bad["violations"])


def test_audit_rejects_unknown_urls_and_wrapper_provenance(admin_client, db_engine) -> None:
    _corpus(db_engine)
    items = [{"source_url": "https://synthetic.example.test/x", "content_span": "y"},
             {"source_url": "https://news.google.com/rss/articles/Z", "content_span": "y"}]
    rep = _run(db_engine, lambda s: audit_evidence_items(s, "claim", items))
    assert len(rep["violations"]) == 2 and "no stored article" in rep["violations"][0]


# ---- API security contract ---------------------------------------------------------------------------------------------

_PUBLIC = {"/health", "/auth/login", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def _routes():
    """Every documented operation (the OpenAPI schema is the reliable route list across FastAPI versions)."""
    for path, ops in app.openapi()["paths"].items():
        if path in _PUBLIC:
            continue
        for method in sorted(m.upper() for m in ops if m.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}):
            yield method, re.sub(r"\{[^}]*\}", lambda mo: str(uuid.uuid4()) if "id" in mo.group(0) else "JCB", path)


def _call(client, method, path):
    return client.request(method, path, json={} if method not in {"GET", "DELETE"} else None)


def test_every_non_public_route_rejects_anonymous_callers(anon_client) -> None:
    seen = 0
    for method, path in _routes():
        seen += 1
        assert _call(anon_client, method, path).status_code in (401, 403), (method, path)
    assert seen >= 30


# Personal/self-service actions that intentionally are NOT admin-only: a viewer manages their own reading list,
# and any signed-in user can run a verification check (it only reads the stored corpus, never writes to it).
_VIEWER_ALLOWED_WRITES = {("PUT", "/articles/{article_id}/saved"), ("DELETE", "/articles/{article_id}/saved"),
                          ("POST", "/engine/fact-check")}


def test_every_write_route_is_admin_only(viewer_client) -> None:
    writes = [(m, p) for m, p in _routes() if m != "GET"]
    assert len(writes) >= 8
    for method, path in writes:
        raw_path = re.sub(r"[0-9a-f-]{36}", "{article_id}", path)
        if (method, raw_path) in _VIEWER_ALLOWED_WRITES:
            # A random article id is never found/saved, so this asserts "not admin-gated" (not 403), not success.
            assert _call(viewer_client, method, path).status_code != 403, (method, path)
            continue
        assert _call(viewer_client, method, path).status_code == 403, (method, path)


def test_read_routes_reject_out_of_bounds_parameters(admin_client) -> None:
    for path in ("/articles?limit=501", "/articles?limit=0", "/articles?offset=-1", "/articles/feed?limit=101",
                 "/search/articles?q=x&limit=101", "/search/articles/evidence?q=x&limit=51",
                 "/intelligence/technology?per_theme=51", "/intelligence/clients/JCB/view?limit=501", "/intelligence/briefing?q=x&limit=51"):
        assert admin_client.get(path).status_code == 422, path


def test_status_and_error_responses_expose_no_secrets_or_paths(admin_client, db_engine) -> None:
    _corpus(db_engine)
    blob = " ".join(admin_client.get(p).text for p in ("/intelligence/pipeline/status", "/intelligence/sources", "/intelligence/clients/NOPE/view",
                                                        "/intelligence/research", "/health", "/auth/me"))
    for needle in ("jwt", "postgresql", "change_me", "password_hash", "C:\\", "/home/", "Traceback", "xenom_intel"):
        assert needle not in blob, needle


def test_offset_pages_are_stable_when_articles_share_identical_timestamps(admin_client, db_engine) -> None:
    """Regression: ties on (published_at, fetched_at, created_at) made offset pages overlap or skip rows."""
    _seed(db_engine, [{"title": f"JCB tied item {i}", "entity": "JCB", "source": "S", "days_ago": 0, "url": f"https://tie{i}.test/x"} for i in range(9)])
    full = [a["id"] for a in _get(admin_client, "/articles", entity_key="jcb", limit=500)]
    for _ in range(3):                                                                  # repeated reads: same order every time
        paged = []
        for off in range(0, 12, 4):
            paged += [a["id"] for a in _get(admin_client, "/articles", entity_key="jcb", limit=4, offset=off)]
        assert paged == full and len(set(paged)) == 9


def test_article_endpoints_never_expose_the_discovery_wrapper_as_url(admin_client, db_engine) -> None:
    _seed(db_engine, [
        {"title": "JCB wrapped one", "entity": "JCB", "source": "S", "url": "https://news.google.com/rss/articles/AAA", "canonical_url": "https://pub.test/one"},
        {"title": "JCB plain two", "entity": "JCB", "source": "S", "url": "https://pub.test/two"},
    ])
    for path in ("/articles", "/articles/feed"):
        rows = _get(admin_client, path)
        assert {a["url"] for a in rows} == {"https://pub.test/one", "https://pub.test/two"}, path
        assert all("news.google.com" not in (a["url"] or "") and "news.google.com" not in (a["canonical_url"] or "") for a in rows)
