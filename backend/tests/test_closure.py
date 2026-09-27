"""Closure pass: theme discovery configuration and scheduling, research class, topic coverage, and the
label -> metric workflows for search and events. Test database only; no network."""
from __future__ import annotations

import asyncio
import csv
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.config.intelligence_themes import TECH_THEMES
from app.config.scheduler import SCHEDULER_CONFIG
from app.config.theme_discovery import THEME_DISCOVERY, client_anchor, theme_specs
from app.services import background_scheduler as bs
from app.services.intelligence_pipeline import PipelineContext
from evaluation import event_quality as eq
from evaluation import search_quality as sq
from tests.test_article_search import _seed
from tests.test_pipeline_core import doc, maker, run_pipeline  # noqa: F401  (maker is a fixture)


def _get(client, path, **params):
    r = client.get(path, params=params)
    assert r.status_code == 200, r.text
    return r.json()


# ---- theme discovery configuration ---------------------------------------------------------------------------------------

def test_every_unevidenced_theme_has_a_discovery_configuration_point() -> None:
    theme_keys = {t.key for t in TECH_THEMES}
    assert set(THEME_DISCOVERY) <= theme_keys
    required = {"multi_agent_ai", "embodied_ai", "digital_twins", "multimodal_ai", "emotion_intent", "trustworthy_ai",
                "hallucination", "humanoids", "ai_models", "blockchain"}                       # the 10 themes with no stored evidence
    assert required <= set(THEME_DISCOVERY)
    specs = theme_specs()
    assert len(specs) == len(THEME_DISCOVERY) * 6 and len({q for _c, _t, q, _u in specs}) == len(specs)
    for client, theme, query, url in specs:
        assert query.startswith(client_anchor(client)) and THEME_DISCOVERY[theme] in query
        assert url.startswith("https://news.google.com/search?q=") and " " not in url and '"' not in url
    assert client_anchor("ISM") == '"India Semiconductor Mission"' and client_anchor("ACE") == '"Action Construction Equipment"'
    assert [s[1] for s in theme_specs(client="JCB")] == list(THEME_DISCOVERY) and len(theme_specs(theme="humanoids")) == 6


def test_scheduler_runs_theme_discovery_on_its_own_cadence_and_isolates_failures(monkeypatch) -> None:
    calls = []

    async def fake_google(maker_, client, query, url, **kw):
        calls.append((client, query, url))
        if "humanoid" in query and client == "JCB":
            raise RuntimeError("theme feed down")
        return {"fetch_status": "ok", "documents_fetched": 2, "documents_parsed": 2, "documents_deduplicated": 0,
                "documents_accepted": 1, "documents_rejected": 1, "adapter_errors": []}

    monkeypatch.setattr(bs, "get_sessionmaker", lambda: object())
    monkeypatch.setattr(bs, "run_google_query", fake_google)
    monkeypatch.setattr(SCHEDULER_CONFIG, "registry_enabled", False)
    monkeypatch.setattr(SCHEDULER_CONFIG, "theme_discovery_enabled", True)
    monkeypatch.setattr(SCHEDULER_CONFIG, "theme_discovery_interval_minutes", 60)
    bs._LAST_THEME_RUN.clear()
    from app.services.registry_ingestion import google_specs
    n_client = len(google_specs())
    first = asyncio.run(bs.run_scheduled_pipeline())
    theme_calls = [c for c in calls if c[1] in {q for _c, _t, q, _u in theme_specs()}]
    assert len(theme_calls) == 60 and len(calls) == n_client + 60
    assert first["theme_discovery"]["queries"] == 59 and first["theme_discovery"]["failed"] == 1
    assert any("theme:humanoids/JCB" in f and "theme feed down" in f for f in first["failed_sources"])
    calls.clear()
    second = asyncio.run(bs.run_scheduled_pipeline())                                     # inside the 60-minute window
    assert len(calls) == n_client and "theme_discovery" not in second
    monkeypatch.setattr(SCHEDULER_CONFIG, "theme_discovery_interval_minutes", 0)
    calls.clear()
    asyncio.run(bs.run_scheduled_pipeline())
    assert len(calls) == n_client + 60
    bs._LAST_THEME_RUN.clear()


def test_theme_discovery_can_be_switched_off(monkeypatch) -> None:
    calls = []

    async def fake_google(maker_, client, query, url, **kw):
        calls.append(query)
        return {"fetch_status": "ok"}

    monkeypatch.setattr(bs, "get_sessionmaker", lambda: object())
    monkeypatch.setattr(bs, "run_google_query", fake_google)
    monkeypatch.setattr(SCHEDULER_CONFIG, "registry_enabled", False)
    monkeypatch.setattr(SCHEDULER_CONFIG, "theme_discovery_enabled", False)
    bs._LAST_THEME_RUN.clear()
    summary = asyncio.run(bs.run_scheduled_pipeline())
    assert "theme_discovery" not in summary and not any(q in {x[2] for x in theme_specs()} for q in calls)


def test_technology_view_exposes_discovery_status_per_theme(admin_client, db_engine) -> None:
    _seed(db_engine, [{"title": "JCB tests robots", "entity": "JCB", "source": "S", "url": "https://d1.test/1"}])
    themes = {t["key"]: t for t in _get(admin_client, "/intelligence/technology")["themes"]}
    dt = themes["digital_twins"]["discovery"]
    assert dt["configured"] and dt["queries"] == 6 and dt["status"] == "NEVER_ATTEMPTED" and dt["last_attempt_at"] is None
    assert "client-relevance gate" in dt["ingestion_path"] and dt["phrase"] == '"digital twin"'
    assert themes["robotics"]["discovery"]["configured"] is False                          # evidenced through client queries

    query = next(q for c, t, q, u in theme_specs() if t == "digital_twins" and c == "JCB")

    async def attempt():
        async with db_engine.begin() as c:
            await c.execute(text("insert into sources (id,name,canonical_url,source_type,tier,is_active) values "
                                 "(gen_random_uuid(), :n, 'https://news.google.com/search?q=x', 'RSS','TIER_2',true)"), {"n": f"Google News: JCB ({query})"})
            await c.execute(text("insert into source_health (id, source_id, checked_at, status, latency_ms) "
                                 "select gen_random_uuid(), id, now(), 'ok', 50 from sources where name like 'Google News: JCB (%digital twin%'"))
    asyncio.run(attempt())
    dt = {t["key"]: t for t in _get(admin_client, "/intelligence/technology")["themes"]}["digital_twins"]["discovery"]
    assert dt["status"] == "ATTEMPTED" and dt["sources_registered"] == 1 and dt["last_attempt_at"]
    assert _get(admin_client, "/intelligence/technology")["themes"][0]["status"] in {"EVIDENCE_FOUND", "NO_EVIDENCE_IN_STORED_CORPUS"}


# ---- research class ---------------------------------------------------------------------------------------------------------

def test_research_source_kind_yields_research_records_and_news_does_not(maker, admin_client) -> None:
    title = "JCB India expands manufacturing capacity at Pune plant"
    run_pipeline(maker, lambda s: [doc(s, title, domain="arxiv.org", url="https://arxiv.org/abs/2609.00001")],
                 ctx=PipelineContext(resolve_media=False, source_kind="research"))
    run_pipeline(maker, lambda s: [doc(s, "JCB India unveils new backhoe loader range for construction customers",
                                       domain="autocarpro.in", url="https://autocarpro.in/news/x1")])
    v = _get(admin_client, "/intelligence/research")
    assert v["status"] == "RESEARCH_RECORDS_FOUND", v                                       # accepted through the client gate
    assert [r["url"] for r in v["records"]] == ["https://arxiv.org/abs/2609.00001"]
    assert v["records"][0]["authors"] == [] and v["records"][0]["authors_status"] == "NOT_STORED"
    assert v["records"][0]["evidence"]["quote"] == title
    assert "autocarpro.in" not in str(v["records"])                                         # client news never appears as research


def test_research_source_type_is_assigned_only_for_research_kind() -> None:
    from app.config.entity_config import ENTITY_CONFIGS
    from app.services.client_relevance import _source_type
    cfg = ENTITY_CONFIGS["JCB"]
    assert _source_type("arxiv.org", cfg, "research", owned=False) == "research"
    assert _source_type("arxiv.org", cfg, None, owned=False) == "news"


# ---- topic coverage is measured, never guessed ----------------------------------------------------------------------------

def test_industry_view_reports_topic_coverage_without_rewriting_rows(admin_client, db_engine) -> None:
    ids = _seed(db_engine, [
        {"title": "JCB robotics automation plant", "entity": "JCB", "source": "S", "url": "https://tp1.test/1"},          # taggable, unlabelled
        {"title": "Quiet note", "summary": "Nothing relevant.", "entity": "JCB", "source": "S", "url": "https://tp2.test/2"},  # not taggable
    ])

    async def label():
        async with db_engine.begin() as c:
            await c.execute(text("update articles set topic='Research' where id=:i"), {"i": ids["Quiet note"]})
    asyncio.run(label())
    v = _get(admin_client, "/intelligence/industry")["topic_coverage"]
    assert (v["accepted_articles"], v["stored_labelled"], v["stored_unlabelled"]) == (2, 1, 1)
    assert v["unlabelled_the_current_classifier_would_label"] == 1 and v["stored_labels_reproduced_by_current_classifier"] == 0
    assert "never rewritten" in v["explanation"]

    async def topics():
        async with db_engine.connect() as c:
            return dict((await c.execute(text("select title, topic from articles"))).all())
    assert asyncio.run(topics()) == {"JCB robotics automation plant": None, "Quiet note": "Research"}      # nothing was written


# ---- workflows: labels -> metrics ------------------------------------------------------------------------------------------

def test_search_labelling_workflow_produces_metrics_only_for_labelled_queries(tmp_path) -> None:
    queries = [{"id": "q1", "query": "jcb", "labelled": False, "expected_article_ids": []},
               {"id": "q2", "query": "ace", "labelled": False, "expected_article_ids": []},
               {"id": "q3", "query": "ism", "labelled": False, "expected_article_ids": []}]
    sheet = tmp_path / "s.csv"
    sq.write_labelling_csv(sheet, queries, {"q1": [{"article_id": "a1", "client": "JCB", "title": "T1"}, {"article_id": "a2", "client": "JCB", "title": "T2"}],
                                            "q2": [{"article_id": "b1", "client": "ACE", "title": "T3"}]})
    assert sq.aggregate([sq.evaluate_query(q, ["a1"]) for q in sq.apply_labels_csv(sheet, queries)])["mrr"] == sq.NOT_MEASURED
    rows = list(csv.reader(open(sheet, encoding="utf-8-sig", newline="")))
    for r in rows[1:]:                                                                       # a human marks a1 relevant, q3 has none
        if r[2] == "a1":
            r[5] = "y"
        if r[0] == "q3" and not r[2]:
            r[5] = "y"
    with open(sheet, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(rows)
    labelled = sq.apply_labels_csv(sheet, queries)
    per = [sq.evaluate_query(q, ["a2", "a1"] if q["id"] == "q1" else ["b1"]) for q in labelled]
    agg = sq.aggregate(per)
    assert [p["status"] for p in per] == ["measured", sq.NOT_MEASURED, "no-match expected"]
    assert agg["queries_measured"] == 1 and agg["mrr"] == 0.5 and agg["recall@5"] == 1.0 and agg["precision@5"] == 0.2
    assert agg["no_match_violations"] == 1                                                   # q3 should be empty but returned b1


def test_event_labelling_workflow_and_validation(tmp_path) -> None:
    events = [{"event_id": f"ev-{i}", "client": "JCB", "article_url": f"https://x.test/{i}", "event_type": "launch",
               "event_time": "2026-09-15T00:00:00+00:00" if i == 0 else "", "source_count": 1, "sentence": f"JCB launches item {i}"}
              for i in range(5)]
    sheet = tmp_path / "e.csv"
    eq.write_event_sheet(sheet, events)
    empty = eq.compute_event_metrics(eq.read_event_labels(sheet, {e["event_id"] for e in events}))
    assert empty["precision"] == eq.NOT_MEASURED and empty["type_accuracy"] == eq.NOT_MEASURED and empty["events_labelled"] == 0

    rows = list(csv.reader(open(sheet, encoding="utf-8-sig", newline="")))
    fill = {"ev-0": ["y", "y", "y", "", ""], "ev-1": ["y", "acquisition", "", "", ""], "ev-2": ["n", "", "", "", "background"],
            "ev-3": ["y", "y", "", "ev-1", ""]}                                                # ev-4 stays unlabelled
    for r in rows[1:]:
        if r[0] in fill:
            r[7:12] = fill[r[0]]
    with open(sheet, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(rows)
    m = eq.compute_event_metrics(eq.read_event_labels(sheet, {e["event_id"] for e in events}))
    assert m["events_total"] == 5 and m["events_labelled"] == 4
    assert m["precision"] == 0.75                                                              # 3 valid of 4 labelled
    assert m["type_accuracy"] == round(2 / 3, 4)                                               # ev-1 mistyped
    assert m["date_accuracy"] == 1.0                                                           # only ev-0 states a date
    assert m["duplicate_rate"] == round(1 / 3, 4)

    def bad(mutate, msg):
        rows2 = [list(r) for r in rows]
        mutate(rows2)
        p = tmp_path / "bad.csv"
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerows(rows2)
        with pytest.raises(eq.EventLabelError, match=msg):
            eq.read_event_labels(p, {e["event_id"] for e in events})
    bad(lambda r: r[1].__setitem__(7, "maybe"), "valid_event")
    bad(lambda r: r[1].__setitem__(8, "sailing"), "correct_type")
    bad(lambda r: (r[1].__setitem__(7, "n"), r[1].__setitem__(8, "launch")), "only apply to valid")
    bad(lambda r: r[1].__setitem__(10, "ev-999"), "not a listed event")
    bad(lambda r: r[2].__setitem__(0, "ev-0"), "duplicate event_id")


def test_fixture_ingestion_is_refused_in_production(admin_client, monkeypatch) -> None:
    from app.config.settings import get_settings
    payload = {"name": "Fixture src", "url": "https://fixture.test/feed", "source_type": "RSS", "tier": "TIER_3",
               "fixture_path": "m4-example-feed.xml"}
    sid = admin_client.post("/sources", json=payload).json()["id"]
    assert admin_client.post(f"/sources/{sid}/fetch").status_code == 200                     # development: fixture ingestion works
    monkeypatch.setenv("ENVIRONMENT", "production")
    get_settings.cache_clear()
    try:
        r = admin_client.post(f"/sources/{sid}/fetch")
    finally:
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        get_settings.cache_clear()
    assert r.status_code == 422 and "disabled in production" in r.text
