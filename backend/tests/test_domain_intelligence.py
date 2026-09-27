"""Technology / industry / competitor / client-summary views, the diverse feed, source status and pipeline status.
All data is seeded into the TEST database only."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.scheduler import SCHEDULER_CONFIG
from app.services.pipeline_status import pipeline_status
from tests.test_article_search import NOW, _seed


def _set_text(db_engine, article_id: str, raw_text: str) -> None:
    async def run():
        async with db_engine.begin() as c:
            await c.execute(text("update articles set raw_text=:r where id=:i"), {"r": raw_text, "i": article_id})
    asyncio.run(run())


def _get(client, path, **params):
    r = client.get(path, params=params)
    assert r.status_code == 200, r.text
    return r.json()


# ---- technology themes ---------------------------------------------------------------------------------------------

def test_technology_view_is_evidence_only_and_reports_empty_themes(admin_client, db_engine) -> None:
    ids = _seed(db_engine, [
        {"title": "JCB tests robots on assembly lines", "summary": "Pilot plant.", "entity": "JCB", "source": "S", "url": "https://t1.test/1"},
        {"title": "Air India maintenance schedule update", "summary": "Nothing technical.", "entity": "ACE", "source": "S", "url": "https://t2.test/2"},
        {"title": "ISM briefing", "summary": "Notes.", "entity": "ISM", "source": "S", "url": "https://t3.test/3"},
    ])
    _set_text(db_engine, ids["ISM briefing"], "&lt;p&gt;Generative AI is discussed.&lt;/p&gt;")   # only inside markup
    v = _get(admin_client, "/intelligence/technology")
    themes = {t["key"]: t for t in v["themes"]}
    assert {"generative_ai", "multi_agent_ai", "embodied_ai", "robotics", "digital_twins", "edge_iot", "multimodal_ai",
            "emotion_intent", "trustworthy_ai", "hallucination", "humanoids"} <= set(themes)      # every required theme listed
    rob = themes["robotics"]
    assert rob["status"] == "EVIDENCE_FOUND" and rob["article_count"] == 1 and rob["by_client"] == {"JCB": 1}
    assert rob["signals"][0]["evidence"]["quote"] == "JCB tests robots on assembly lines"
    assert themes["multi_agent_ai"]["status"] == "NO_EVIDENCE_IN_STORED_CORPUS" and themes["multi_agent_ai"]["signals"] == []
    assert themes["ai_ml_general"]["article_count"] == 0                                        # "Air India", "maintenance": no substring hits
    assert themes["generative_ai"]["article_count"] == 0 and v["excluded_without_clean_evidence"] >= 1   # markup-only evidence is not evidence
    assert "client-centred" in v["note"]


def test_technology_counts_are_not_truncated_by_the_listing_limit(admin_client, db_engine) -> None:
    _seed(db_engine, [{"title": f"JCB robot model {i} launched", "entity": "JCB", "source": "S", "url": f"https://r{i}.test/x", "days_ago": i}
                      for i in range(7)])
    rob = {t["key"]: t for t in _get(admin_client, "/intelligence/technology", per_theme=2)["themes"]}["robotics"]
    assert rob["article_count"] == 7 and len(rob["signals"]) == 2                                # real total, limited listing
    dates = [s["published_at"] for s in rob["signals"]]
    assert dates == sorted(dates, reverse=True)


# ---- industry ------------------------------------------------------------------------------------------------------

def test_industry_view_is_client_scoped_with_quotes(admin_client, db_engine) -> None:
    _seed(db_engine, [
        {"title": "JCB excavator demand rises", "entity": "JCB", "source": "S", "url": "https://i1.test/1"},
        {"title": "ISM notes excavator imports", "entity": "ISM", "source": "S", "url": "https://i2.test/2"},
    ])
    v = _get(admin_client, "/intelligence/industry")
    clients = {c["client"]: c for c in v["clients"]}
    assert set(clients) == {"JCB", "ACE", "Definedge", "ISM", "VIPS", "Vikas"}
    jcb = {t["term"]: t for t in clients["JCB"]["industry_terms"]}
    assert jcb["excavator"]["article_count"] == 1 and jcb["excavator"]["signals"][0]["evidence"]["quote"] == "JCB excavator demand rises"
    assert clients["ISM"]["accepted_articles"] == 1 and all(t["term"] != "excavator" for t in clients["ISM"]["industry_terms"])
    assert v["taxonomy_labels"]["Unlabelled"] == 2 and "ORG-only" in v["note"]


# ---- competitors ---------------------------------------------------------------------------------------------------

def test_competitors_are_only_what_is_configured(admin_client, db_engine) -> None:
    empty = _get(admin_client, "/intelligence/competitors")
    assert empty["configured"] is False and empty["competitors"] == [] and "never inferred" in empty["reason"]
    key = f"rival-{uuid.uuid4().hex[:8]}"

    async def setup():
        async with db_engine.begin() as c:
            await c.execute(text("insert into entities (id, entity_type, canonical_name, canonical_key, status) "
                                 "values (gen_random_uuid(), 'ORG', :n, :k, 'needs_review')"), {"n": "Rivalco", "k": key})
            await c.execute(text("insert into relationships (id, source_entity_id, target_entity_id, relationship_type, verification_status) "
                                 "select gen_random_uuid(), r.id, j.id, 'COMPETITOR_OF', 'unverified' from entities r, entities j "
                                 "where r.canonical_key=:k and j.canonical_key='jcb'"), {"k": key})
    asyncio.run(setup())
    try:
        ids = _seed(db_engine, [{"title": "Rivalco launches new loader", "entity": "JCB", "source": "S", "url": "https://c1.test/1"}])

        async def link():
            async with db_engine.begin() as c:
                await c.execute(text("insert into entity_mentions (id, entity_id, article_id, mention_text) select gen_random_uuid(), e.id, :a, 'Rivalco' "
                                     "from entities e where e.canonical_key=:k"), {"a": ids["Rivalco launches new loader"], "k": key})
        asyncio.run(link())
        got = _get(admin_client, "/intelligence/competitors")
        assert got["configured"] is True and got["competitors"][0]["competitor"] == "Rivalco"
        assert got["competitors"][0]["competitor_of"] == "JCB" and got["competitors"][0]["article_count"] == 1
        assert got["competitors"][0]["signals"][0]["evidence"]["quote"] == "Rivalco launches new loader"
    finally:
        async def cleanup():
            async with db_engine.begin() as c:
                await c.execute(text("delete from relationships where source_entity_id in (select id from entities where canonical_key=:k)"), {"k": key})
                await c.execute(text("delete from entities where canonical_key=:k"), {"k": key})
        asyncio.run(cleanup())


# ---- client summary: DB -> API consistency ---------------------------------------------------------------------------

def test_client_summary_matches_the_articles_api(admin_client, db_engine) -> None:
    _seed(db_engine, [
        {"title": "JCB one", "entity": "JCB", "source": "S", "url": "https://s1.test/1"},
        {"title": "JCB two", "entity": "JCB", "source": "S", "url": "https://s2.test/2"},
        {"title": "ISM one", "entity": "ISM", "source": "S", "url": "https://s3.test/3"},
        {"title": "rejected one", "entity": "ACE", "source": "S", "status": "rejected", "gate": False},
    ])
    v = _get(admin_client, "/intelligence/clients/summary")
    by = {c["client"]: c for c in v["clients"]}
    assert (by["JCB"]["accepted_articles"], by["ISM"]["accepted_articles"], by["ACE"]["accepted_articles"]) == (2, 1, 0)
    assert v["total_accepted"] == 3 and v["total_rejected"] == 1
    for name in ("JCB", "ISM", "ACE", "VIPS"):                                                      # summary == /articles?entity_key count
        assert by[name]["accepted_articles"] == len(_get(admin_client, "/articles", entity_key=name.lower(), limit=500))
    assert by["JCB"]["publishers"] == 2 and by["JCB"]["source_classes"] == {"trusted_news": 2}


# ---- diverse feed --------------------------------------------------------------------------------------------------

def test_feed_is_diverse_across_clients_and_publishers_and_deterministic(admin_client, db_engine) -> None:
    rows = [{"title": f"JCB item {i}", "entity": "JCB", "source": "S", "days_ago": i, "url": f"https://jcb{i}.test/x"} for i in range(6)]
    rows += [{"title": "ISM item", "entity": "ISM", "source": "S", "days_ago": 20, "url": "https://ism.test/x"},
             {"title": "ACE item", "entity": "ACE", "source": "S", "days_ago": 30, "url": "https://ace.test/x"}]
    _seed(db_engine, rows)
    newest = _get(admin_client, "/articles", limit=4)                                            # what "newest N" would show
    assert {a["entity_names"][0] for a in newest} == {"JCB"}
    feed = _get(admin_client, "/articles/feed", limit=4)
    assert {a["entity_names"][0] for a in feed} == {"JCB", "ISM", "ACE"}                         # every client with data appears
    assert len(feed) == 4 and len({a["id"] for a in feed}) == 4
    assert feed == _get(admin_client, "/articles/feed", limit=4)
    assert admin_client.get("/articles/feed", params={"limit": 0}).status_code == 422


def test_feed_caps_articles_per_publisher(admin_client, db_engine) -> None:
    _seed(db_engine, [{"title": f"JCB same publisher {i}", "entity": "JCB", "source": "S", "days_ago": i,
                       "url": f"https://samepub.test/{i}"} for i in range(5)])
    assert len(_get(admin_client, "/articles/feed", limit=10, max_per_publisher=2)) == 2


# ---- source status: configured vs healthy vs producing -----------------------------------------------------------

def test_source_status_keeps_configured_health_and_productivity_separate(admin_client, db_engine) -> None:
    _seed(db_engine, [
        {"title": "JCB ok one", "entity": "JCB", "source": "Producing src", "url": "https://p1.test/1"},
        {"title": "JCB ok two", "entity": "JCB", "source": "Producing src", "url": "https://p2.test/2"},
        {"title": "ACE only rejected", "entity": "ACE", "source": "Rejecting src", "status": "rejected", "gate": False,
         "url": "https://p3.test/3"},
    ])

    async def add():
        async with db_engine.begin() as c:
            await c.execute(text("insert into sources (id, name, canonical_url, source_type, tier, is_active) values "
                                 "(gen_random_uuid(),'Empty src','https://e.test/f','RSS','TIER_2',true),"
                                 "(gen_random_uuid(),'Off src','https://off.test/f','RSS','TIER_2',false)"))
            now = datetime.now(timezone.utc)
            for status, mins in (("ok", 30), ("error", 20), ("timeout", 10)):                   # oldest ok, then two failures
                await c.execute(text("insert into source_health (id, source_id, checked_at, status, latency_ms, error_message) "
                                     "select gen_random_uuid(), id, :t, :s, 120, :m from sources where name='Producing src'"),
                                {"t": now - timedelta(minutes=mins), "s": status, "m": None if status == "ok" else "HTTP 503"})
    asyncio.run(add())
    v = _get(admin_client, "/intelligence/sources")
    by = {s["name"]: s for s in v["sources"]}
    prod = by["Producing src"]
    assert prod["productivity_state"] == "PRODUCING" and prod["accepted"] == 2 and prod["rejected"] == 0 and prod["acceptance_rate"] == 1.0
    assert prod["health_status"] == "UNHEALTHY" and prod["consecutive_failures"] == 2 and prod["last_health_error"] == "HTTP 503"
    assert prod["clients"] == ["jcb"] and prod["configured_active"] is True
    rej = by["Rejecting src"]
    assert rej["productivity_state"] == "NO_ACCEPTED" and rej["acceptance_rate"] == 0.0 and rej["health_status"] == "UNKNOWN"
    assert by["Empty src"]["productivity_state"] == "NO_ARTICLES" and by["Empty src"]["acceptance_rate"] is None
    assert by["Off src"]["productivity_state"] == "INACTIVE" and by["Off src"]["configured_active"] is False
    assert v["by_health"] == {"UNHEALTHY": 1, "UNKNOWN": 3} and "never measured" in v["note"]


# ---- pipeline status (live / continuous visibility) ---------------------------------------------------------------

def _status(db_engine, now):
    async def run():
        async with AsyncSession(db_engine) as s:
            return await pipeline_status(s, now)
    return asyncio.run(run())


def test_pipeline_status_states(admin_client, db_engine, monkeypatch) -> None:
    assert _status(db_engine, datetime.now(timezone.utc)).continuous_updates == "NO_DATA"
    _seed(db_engine, [{"title": "JCB fresh", "entity": "JCB", "source": "S", "url": "https://f.test/1"}])
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(SCHEDULER_CONFIG, "enabled", False)
    off = _status(db_engine, now)
    assert off.continuous_updates == "SCHEDULER_OFF" and off.scheduler["enabled"] is False
    monkeypatch.setattr(SCHEDULER_CONFIG, "enabled", True)
    assert _status(db_engine, now).continuous_updates == "ACTIVE"
    stale = _status(db_engine, now + timedelta(hours=2))                                          # limit = 3 intervals = 30 min
    assert stale.continuous_updates == "STALE" and stale.hours_since_latest_ingest >= 2
    jcb = next(c for c in stale.clients if c.client == "JCB")
    assert jcb.accepted_articles == 1 and jcb.latest_ingested_at is not None
    assert next(c for c in stale.clients if c.client == "VIPS").accepted_articles == 0


def test_status_endpoints_allow_signed_in_viewers(viewer_client) -> None:
    assert viewer_client.get("/intelligence/pipeline/status").status_code == 200
    assert viewer_client.get("/intelligence/sources").status_code == 200
    assert viewer_client.get("/intelligence/technology").status_code == 200


def test_anonymous_cannot_read_the_views(anon_client) -> None:
    for path in ("/intelligence/technology", "/intelligence/industry", "/intelligence/competitors",
                 "/intelligence/clients/summary", "/intelligence/sources", "/intelligence/pipeline/status", "/articles/feed"):
        assert anon_client.get(path).status_code in (401, 403), path


# ---- final pass: industry entities resolve to stored articles; technology splits evidence / no evidence ----------------

def test_registered_industries_resolve_to_articles_only_where_text_supports_them(admin_client, db_engine) -> None:
    key = f"industry-{uuid.uuid4().hex[:6]}"

    async def add():
        async with db_engine.begin() as c:
            for name, k in (("Construction", key + "-c"), ("Aerospace", key + "-a")):
                await c.execute(text("insert into entities (id, entity_type, canonical_name, canonical_key, status) "
                                     "values (gen_random_uuid(), 'INDUSTRY', :n, :k, 'needs_review')"), {"n": name, "k": k})
    asyncio.run(add())
    try:
        _seed(db_engine, [
            {"title": "Construction spending rises for JCB dealers", "entity": "JCB", "source": "S", "url": "https://ind1.test/1"},
            {"title": "ISM update without sector words", "entity": "ISM", "source": "S", "url": "https://ind2.test/2"},
        ])
        v = _get(admin_client, "/intelligence/industry")
        ents = {e["industry"]: e for e in v["industry_entities"]}
        con = ents["Construction"]
        assert con["status"] == "EVIDENCE_FOUND" and con["article_count"] == 1 and con["by_client"] == {"JCB": 1}
        assert con["signals"][0]["evidence"]["quote"] == "Construction spending rises for JCB dealers"
        assert con["signals"][0]["url"] == "https://ind1.test/1"                                    # resolves to the stored article
        assert ents["Aerospace"]["status"] == "NO_EVIDENCE_IN_STORED_CORPUS" and ents["Aerospace"]["signals"] == []
        assert set(v["registered_industries"]) >= {"Construction", "Aerospace"}                       # every listed entity is also resolved
        assert set(v["registered_industries"]) == set(ents)
    finally:
        async def cleanup():
            async with db_engine.begin() as c:
                await c.execute(text("delete from entities where canonical_key like :k"), {"k": key + "%"})
        asyncio.run(cleanup())


def test_technology_view_separates_evidenced_from_unavailable_themes(admin_client, db_engine) -> None:
    _seed(db_engine, [{"title": "JCB pilots humanoid robots at its plant", "entity": "JCB", "source": "S", "url": "https://th1.test/1"}])
    v = _get(admin_client, "/intelligence/technology")
    by = {t["key"]: t for t in v["themes"]}
    assert set(v["themes_with_evidence"]) == {"robotics", "humanoids"}
    assert set(v["themes_with_evidence"]) | set(v["themes_without_evidence"]) == set(by)
    assert not set(v["themes_with_evidence"]) & set(v["themes_without_evidence"])
    assert all(by[k]["status"] == "NO_EVIDENCE_IN_STORED_CORPUS" and by[k]["signals"] == [] for k in v["themes_without_evidence"])
    assert all(by[k]["signals"] and by[k]["status"] == "EVIDENCE_FOUND" for k in v["themes_with_evidence"])
