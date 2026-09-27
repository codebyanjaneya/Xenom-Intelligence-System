"""Finalization: PDF-required search filters and suggestions, draft insights, nDCG, rejection reasons, production
guards for development mechanisms, claim provenance flags, and a structural (not regex) provenance/markup walk over
every article-carrying endpoint. Test database only."""
from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import text

from app.config.settings import Settings, get_settings, ProductionConfigError, validate_for_production
from app.services.markup import has_markup
from evaluation import search_quality as sq
from tests.test_article_search import _seed
from tests.test_claims import _payload as claim_payload
from tests.test_claims import _setup as claim_setup
from tests.test_client_intelligence import _corpus


def _get(client, path, **params):
    r = client.get(path, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _sql(db_engine, sql, params=None):
    async def run():
        async with db_engine.begin() as c:
            res = await c.execute(text(sql), params or {})
            return res.all() if res.returns_rows else None
    return asyncio.run(run())


# ---- search: filters and suggestions from the PDF (section 8) --------------------------------------------------------

def _filter_corpus(db_engine):
    ids = _seed(db_engine, [
        {"title": "JCB excavator official newsroom", "entity": "JCB", "source": "Official src", "url": "https://o.test/1"},
        {"title": "JCB excavator trade press story", "entity": "JCB", "source": "Trade src", "url": "https://t.test/2"},
        {"title": "ISM excavator imports note about JCB dealers", "entity": "ISM", "source": "Trade src", "url": "https://t.test/3"},
    ])
    _sql(db_engine, "update sources set tier='TIER_1' where name='Official src'")
    _sql(db_engine, "update articles set score_reason = jsonb_set(score_reason::jsonb, '{source_type}', '\"official\"')::text where title like '%official newsroom%'")
    _sql(db_engine, "update articles set topic='Industry|Company' where title like '%trade press%'")
    return ids


def test_search_filters_by_tier_source_class_and_topic(admin_client, db_engine) -> None:
    ids = _filter_corpus(db_engine)

    def titles(**p):
        return sorted(h["title"] for h in _get(admin_client, "/search/articles", q="excavator", **p)["results"])
    assert len(titles()) == 3
    assert titles(tier="TIER_1") == ["JCB excavator official newsroom"]
    assert titles(source_class="official") == ["JCB excavator official newsroom"]
    assert titles(source_class="trusted_news") == ["ISM excavator imports note about JCB dealers", "JCB excavator trade press story"]
    assert titles(topic="industry") == ["JCB excavator trade press story"]
    assert titles(tier="TIER_1", topic="industry") == []                                       # filters combine (AND)
    hit = _get(admin_client, "/search/articles", q="official newsroom")["results"][0]
    assert hit["tier"] == "TIER_1" and hit["source_class"] == "official" and hit["relevance"]["decision"] == "accepted"
    ev = _get(admin_client, "/search/articles/evidence", q="excavator", tier="TIER_1")
    assert [h["tier"] for h in ev["results"]] == ["TIER_1"]
    for bad in ({"tier": "TIER_9"}, {"source_class": "blog"}, {"topic": "x" * 61}):
        assert admin_client.get("/search/articles", params={"q": "excavator", **bad}).status_code == 422


def test_clients_named_in_the_query_are_suggested_never_applied(admin_client, db_engine) -> None:
    _filter_corpus(db_engine)
    r = _get(admin_client, "/search/articles", q="JCB excavator")
    assert r["suggested_filters"] == {"client": ["jcb"]}
    assert {h["client"] for h in r["results"]} == {"JCB", "ISM"}                               # ISM article still returned: not filtered
    assert _get(admin_client, "/search/articles", q="excavator")["suggested_filters"] == {}
    applied = _get(admin_client, "/search/articles", q="JCB excavator", entity_key="jcb")
    assert applied["suggested_filters"] == {} and {h["client"] for h in applied["results"]} == {"JCB"}
    assert _get(admin_client, "/search/articles/evidence", q="JCB excavator")["suggested_filters"] == {"client": ["jcb"]}


# ---- insights are drafts; nDCG; rejection reasons -------------------------------------------------------------------------

def test_insights_are_drafts_until_reviewed(admin_client, db_engine) -> None:
    _corpus(db_engine)
    insights = _get(admin_client, "/intelligence/clients/JCB/insights")
    assert insights and all(i["status"] == "draft" for i in insights)


def test_ndcg_and_metric_keys() -> None:
    exp = {"a", "b"}
    assert sq.ndcg_at_k(["a", "b", "x"], exp, 5) == 1.0
    assert sq.ndcg_at_k(["x", "a", "b"], exp, 5) == pytest.approx((1 / 1.5849625 + 1 / 2) / (1 + 1 / 1.5849625), abs=1e-3)
    assert sq.ndcg_at_k(["x", "y"], exp, 5) == 0.0
    q = {"id": "q", "query": "q", "labelled": True, "expected_article_ids": ["a", "b"]}
    assert sq.evaluate_query(q, ["a", "x", "b"])["ndcg@5"] == round(sq.ndcg_at_k(["a", "x", "b"], exp, 5), 4)
    assert sq.aggregate([sq.evaluate_query({**q, "labelled": False}, [])])["ndcg@5"] == sq.NOT_MEASURED


def test_pipeline_status_lists_quality_gate_rejection_reasons(admin_client, db_engine) -> None:
    _seed(db_engine, [{"title": f"rej {i}", "entity": "JCB", "source": "S", "status": "rejected", "gate": False} for i in range(3)])
    _sql(db_engine, "update articles set score_reason = :r where title in ('rej 0','rej 1')", {"r": json.dumps({"decision": "rejected", "stage": "relevance", "reason": "NO_ENTITY_EVIDENCE"})})
    _sql(db_engine, "update articles set score_reason = :r where title = 'rej 2'", {"r": json.dumps({"decision": "rejected", "stage": "quality", "reason": "BELOW_THRESHOLD"})})
    v = _get(admin_client, "/intelligence/pipeline/status")
    assert v["rejection_reasons"] == {"relevance:NO_ENTITY_EVIDENCE": 2, "quality:BELOW_THRESHOLD": 1}


# ---- development mechanisms cannot become the production data path ---------------------------------------------------------

def test_unsafe_production_configuration_refuses_to_start() -> None:
    safe = dict(environment="production", jwt_secret="x" * 40, database_url="postgresql+asyncpg://u:strongpw@db/x", search_provider="brave")
    validate_for_production(Settings(_env_file=None, **safe))
    validate_for_production(Settings(_env_file=None, **{**safe, "environment": "development", "jwt_secret": "dev-insecure-x"}))   # dev is unrestricted
    for override, needle in (({"jwt_secret": "dev-insecure-secret-change-me-please-32-bytes-min"}, "JWT_SECRET"),
                             ({"jwt_secret": "short"}, "JWT_SECRET"),
                             ({"database_url": "postgresql+asyncpg://xenom:change_me_local@localhost/x"}, "DATABASE_URL"),
                             ({"search_provider": "fixture"}, "SEARCH_PROVIDER")):
        with pytest.raises(ProductionConfigError, match=needle):
            validate_for_production(Settings(_env_file=None, **{**safe, **override}))


def test_create_app_validates_production_settings(monkeypatch) -> None:
    from app.main import create_app
    monkeypatch.setenv("ENVIRONMENT", "production")
    get_settings.cache_clear()
    try:
        with pytest.raises(ProductionConfigError):
            create_app()                                                                   # test defaults are development secrets
    finally:
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        get_settings.cache_clear()


def test_fixture_search_provider_is_refused_in_production(monkeypatch) -> None:
    from app.providers.search import SearchProviderError
    from app.services.search import get_search_provider
    monkeypatch.setenv("SEARCH_PROVIDER", "fixture")
    get_settings.cache_clear()
    assert get_search_provider().name != "unconfigured"                                    # development: selectable
    monkeypatch.setenv("ENVIRONMENT", "production")
    get_settings.cache_clear()
    try:
        with pytest.raises(SearchProviderError, match="disabled in production"):
            get_search_provider()
    finally:
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        get_settings.cache_clear()


def test_fixture_backed_sources_cannot_be_created_or_edited_in_production(admin_client, monkeypatch) -> None:
    payload = {"name": "F", "url": "https://f.test/feed", "source_type": "RSS", "tier": "TIER_3", "fixture_path": "m4-example-feed.xml"}
    ok = admin_client.post("/sources", json=payload)
    assert ok.status_code == 201                                                           # development
    plain = admin_client.post("/sources", json={**payload, "url": "https://g.test/feed", "fixture_path": None}).json()["id"]
    monkeypatch.setenv("ENVIRONMENT", "production")
    get_settings.cache_clear()
    try:
        assert admin_client.post("/sources", json={**payload, "url": "https://h.test/feed"}).status_code == 422
        r = admin_client.patch(f"/sources/{plain}", json={"fixture_path": "m4-example-feed.xml"})
        assert r.status_code == 422 and "disabled in production" in r.text
        assert admin_client.patch(f"/sources/{plain}", json={"name": "renamed"}).status_code == 200      # other edits still work
    finally:
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        get_settings.cache_clear()


# ---- claims: provenance flag ----------------------------------------------------------------------------------------------

def test_claim_list_flags_unresolved_legacy_claims(admin_client, db_engine) -> None:
    _sql(db_engine, "delete from claims")                                                    # test DB only: isolate from other tests' claims
    body, event = claim_setup(admin_client, db_engine)
    good = admin_client.post("/claims", json=claim_payload(body, event, claim_text="A valid stored-path claim."))
    assert good.status_code == 201
    legacy = json.dumps([{"source_url": "https://synthetic.example.test/x", "provider": "fixture", "title": "t", "snippet": "s",
                          "published_at": None, "discovered_at": "2026-01-01T00:00:00Z", "relevance_score": 1.0, "content_span": "s"}])
    _sql(db_engine, "insert into claims (id, claim_text, claim_key, entity_key, event_key, evidence_json, verification_status, created_at, updated_at) "
                    "values (gen_random_uuid(), 'Legacy synthetic claim', 'legacy-key-1', 'x', null, cast(:e as jsonb), 'unverified', now(), now())", {"e": legacy})
    status = {c["claim_text"]: c["provenance_status"] for c in _get(admin_client, "/claims")}
    assert status == {"A valid stored-path claim.": "VERIFIED_IN_STORED_CORPUS", "Legacy synthetic claim": "UNRESOLVED"}
    _sql(db_engine, "delete from claims")


# ---- structural provenance / markup walk (no regex over pretty JSON) --------------------------------------------------------

DISPLAY_KEYS = {"title", "summary", "snippet", "quote", "content_span", "page_title", "sentence"}


def _walk(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v, f"{path}[]")
    elif isinstance(node, str):
        yield path, node


def test_no_article_endpoint_exposes_a_wrapper_url_or_markup(admin_client, db_engine) -> None:
    ids = _seed(db_engine, [
        {"title": "JCB launches wrapped backhoe today", "summary": "JCB launched a backhoe.", "entity": "JCB", "source": "S",
         "url": "https://news.google.com/rss/articles/WRAP1", "canonical_url": "https://pub.test/wrapped"},
        {"title": "&lt;b&gt;JCB unveils marked up loader&lt;/b&gt;", "summary": "&lt;p&gt;JCB unveiled a loader.&lt;/p&gt;", "entity": "JCB", "source": "S",
         "url": "https://news.google.com/rss/articles/WRAP2", "canonical_url": "https://pub.test/markup"},
    ])
    _sql(db_engine, "update articles set raw_text = :r where id = :i", {"i": ids["JCB launches wrapped backhoe today"],
         "r": "&lt;p class=&quot;x&quot;&gt;JCB India unveiled products.&lt;/p&gt; JCB launched a clean sentence about the backhoe here."})
    gets = ["/articles", "/articles/feed", "/search/articles?q=JCB", "/search/articles/evidence?q=JCB", "/intelligence/clients/JCB/view",
            "/intelligence/clients/JCB/briefing", "/intelligence/briefing?q=JCB", "/intelligence/technology", "/intelligence/industry",
            "/intelligence/competitors", "/intelligence/research", "/claims"]
    bodies = [(g, admin_client.get(g).json()) for g in gets]
    bodies.append(("POST /intelligence/extract", admin_client.post("/intelligence/extract", json={"query": "JCB launches"}).json()))
    seen_strings = 0
    for name, body in bodies:
        for path, value in _walk(body):
            seen_strings += 1
            assert "news.google.com" not in value, (name, path, value)                    # no discovery wrapper anywhere
            if path.rsplit(".", 1)[-1].rstrip("[]") in DISPLAY_KEYS:
                assert not has_markup(value), (name, path, value)                          # no markup in display / evidence values
    assert seen_strings > 200
    art = _get(admin_client, "/articles")
    assert {a["url"] for a in art} == {"https://pub.test/wrapped", "https://pub.test/markup"}
    assert {a["canonical_url"] for a in art} == {"https://pub.test/wrapped", "https://pub.test/markup"}


# ---- closure defects: manual ingestion uses the scheduler path; scheduler stop; why-matched on plain search ------------------

def test_manual_pipeline_run_delegates_to_the_scheduler_path(admin_client, monkeypatch) -> None:
    from app.api import intelligence as api_mod
    from app.services.registry_ingestion import google_specs
    calls = []

    async def fake(maker, client, query, url, **kw):
        calls.append((client, query, url, kw["gate"], kw["resolve_media"]))
        if len(calls) == 1:
            raise RuntimeError("x" * 500)
        if len(calls) == 2:
            return {"fetch_status": "failed", "fetch_error": "HTTPError: 503"}
        return {"fetch_status": "ok", "documents_fetched": 4, "documents_parsed": 4, "documents_cleaned": 4, "documents_deduplicated": 1,
                "documents_tagged_entity": 2, "documents_tagged_topic": 2, "documents_scored": 2, "documents_accepted": 1, "documents_rejected": 1}

    monkeypatch.setattr(api_mod, "run_google_query", fake)
    r = admin_client.post("/intelligence/pipeline/run", json={"client_canonical_name": "JCB"})
    assert r.status_code == 200, r.text
    body = r.json()
    specs = google_specs("JCB")
    assert [(c[0], c[1], c[2]) for c in calls] == [(c, q, u) for c, q, u in specs]           # registry URLs, in order, client context
    assert all(c[3] == 40 and c[4] is True for c in calls)                                    # the scheduler's gate, media on
    n_ok = len(specs) - 2
    assert (body["fetched"], body["accepted"], body["rejected"]) == (4 * n_ok, n_ok, n_ok) and body["clients_processed"] == ["JCB"]
    assert len(body["errors"]) == 2 and all(len(e) <= 230 for e in body["errors"]) and "HTTPError: 503" in body["errors"][1]
    calls.clear()
    assert admin_client.post("/intelligence/pipeline/run", json={"client_canonical_name": "JCB", "quality_gate_threshold": 55}).status_code == 200
    assert {c[3] for c in calls} == {55}
    assert admin_client.post("/intelligence/pipeline/run", json={"client_canonical_name": "NOPE"}).status_code == 400
    assert admin_client.post("/intelligence/pipeline/run", json={"quality_gate_threshold": 101}).status_code == 422


def test_scheduler_stop_cancels_its_task(monkeypatch) -> None:
    from app.services import background_scheduler as bs

    async def scenario():
        started = asyncio.Event()

        async def fake_run():
            started.set()
            await asyncio.sleep(3600)

        monkeypatch.setattr(bs, "run_scheduled_pipeline", fake_run)
        monkeypatch.setattr(bs.SCHEDULER_CONFIG, "enabled", True)
        runner = bs.SchedulerRunner()
        await runner.start()
        task = runner.task
        await asyncio.wait_for(started.wait(), 5)
        await runner.stop()
        await asyncio.sleep(0)
        return task, runner

    task, runner = asyncio.run(scenario())
    assert task is not None and (task.cancelled() or task.done()) and runner.task is None and runner.status()["next_run_at"] is None


def test_plain_search_reports_why_it_matched(admin_client, db_engine) -> None:
    _seed(db_engine, [{"title": "JCB launches excavators", "summary": "New range.", "entity": "JCB", "source": "S", "url": "https://mt.test/1"}])
    hit = _get(admin_client, "/search/articles", q="launch excavator crane")["results"][0]
    assert hit["matched_terms"] == ["excavator", "launch"]                                    # literal query terms present; 'crane' is not


def test_theme_discovery_flows_through_the_real_pipeline_into_the_technology_view(admin_client, db_engine, monkeypatch) -> None:
    """Scheduler -> theme query -> fetch (faked) -> clean/dedupe/entity/quality gate -> DB -> literal-match theme evidence."""
    from datetime import datetime, timedelta, timezone
    import uuid as _uuid
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.adapters.ingestion.base import NormalizedDocument
    from app.config.scheduler import SCHEDULER_CONFIG
    from app.config.theme_discovery import theme_specs
    from app.services import background_scheduler as bs
    from app.services import registry_ingestion as ri

    maker = async_sessionmaker(db_engine, expire_on_commit=False)
    target = next(q for c, t, q, u in theme_specs() if t == "digital_twins" and c == "JCB")
    now = datetime.now(timezone.utc)
    title = "JCB India deploys digital twin technology at Pune manufacturing plant"

    async def fake_fetch(self, **kw):
        if kw["query"] != target:
            return []
        return [NormalizedDocument(source_id=_uuid.UUID(kw["source_id"]), canonical_url="https://autocarpro.in/news/digital-twin-1", title=title,
                                   published_at=now - timedelta(hours=3), retrieved_at=now, raw_content=title, normalized_content=title,
                                   content_type="text/html", metadata={"publisher_domain": "autocarpro.in"})]

    original = ri.run_google_query

    async def no_media(maker_, client, query, url, **kw):
        kw["resolve_media"] = False
        return await original(maker_, client, query, url, **kw)

    monkeypatch.setattr(ri.GoogleNewsAdapter, "fetch", fake_fetch)
    monkeypatch.setattr(bs, "run_google_query", no_media)
    monkeypatch.setattr(bs, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(SCHEDULER_CONFIG, "registry_enabled", False)
    monkeypatch.setattr(SCHEDULER_CONFIG, "theme_discovery_enabled", True)
    bs._LAST_THEME_RUN.clear()
    summary = asyncio.run(bs.run_scheduled_pipeline())
    bs._LAST_THEME_RUN.clear()
    assert summary["theme_discovery"]["queries"] == 60 and summary["theme_discovery"]["accepted"] == 1

    view = {t["key"]: t for t in _get(admin_client, "/intelligence/technology")["themes"]}
    twin = view["digital_twins"]
    assert twin["status"] == "EVIDENCE_FOUND" and twin["article_count"] == 1 and twin["by_client"] == {"JCB": 1}
    assert twin["signals"][0]["url"] == "https://autocarpro.in/news/digital-twin-1" and twin["signals"][0]["evidence"]["quote"] == title
    assert twin["discovery"]["status"] == "ATTEMPTED" and twin["discovery"]["sources_registered"] == 6 and twin["discovery"]["last_attempt_at"]
    other = view["humanoids"]                                                                # attempted, nothing found: explicit no-evidence
    assert other["status"] == "NO_EVIDENCE_IN_STORED_CORPUS" and other["signals"] == [] and other["discovery"]["status"] == "ATTEMPTED"


def test_nul_byte_in_a_query_is_rejected_not_a_server_error(admin_client) -> None:
    for path in ("/search/articles/evidence?q=a%00b", "/search/articles?q=%00", "/intelligence/briefing?q=%00"):
        assert admin_client.get(path).status_code == 422
    assert admin_client.post("/intelligence/extract", json={"query": "a" + chr(0) + "b"}).status_code == 422


def test_two_letter_query_terms_get_evidence_spans() -> None:
    from app.services.article_evidence import collect_terms
    assert collect_terms("AI", None) == ["ai"]
    assert collect_terms("the JCB in India", None) == ["india", "jcb"]


def test_production_refuses_unknown_or_test_only_ai_providers() -> None:
    base = dict(environment="production", search_provider="unconfigured", jwt_secret="x" * 40, database_url="postgresql+asyncpg://u:strongpw@db/x")
    validate_for_production(Settings(**base, embedding_provider="local", llm_provider="none"))
    for bad in ({"embedding_provider": "test"}, {"llm_provider": "test"}, {"llm_provider": "anthropic"}):
        with pytest.raises(ProductionConfigError):
            validate_for_production(Settings(**base, **bad))
