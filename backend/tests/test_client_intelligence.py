"""Client view, insights, opportunities, alerts, briefings, temporal events and the traceability audit.
Every test seeds stored articles in the TEST database only."""
from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.client_engine import client_intelligence
from app.services.trace_audit import audit_response
from tests.test_article_search import _seed


def _corpus(db_engine):
    """JCB: 3 distinct launches, 1 launch from 2 publishers, 1 funding round from 2 publishers, 1 old appointment.
    Test article dates are NOW (2026-09-01) minus days_ago."""
    return _seed(db_engine, [
        {"title": "JCB launches backhoe alpha model for farms", "entity": "JCB", "source": "S", "days_ago": 1,
         "url": "https://siteA.test/1"},
        {"title": "JCB unveils excavator beta series for mining", "entity": "JCB", "source": "S", "days_ago": 3,
         "url": "https://siteB.test/2"},
        {"title": "JCB introduces compact loader gamma line", "entity": "JCB", "source": "S", "days_ago": 5,
         "url": "https://siteC.test/3"},
        {"title": "JCB India launches 52 tonne excavator largest machine", "entity": "JCB", "source": "S", "days_ago": 2,
         "url": "https://siteD.test/4"},
        {"title": "JCB India launched 52 tonne excavator largest machine - Trade Mag", "entity": "JCB", "source": "S",
         "days_ago": 2, "url": "https://siteE.test/5"},
        {"title": "JCB raises funding round of 100 crore from investors", "entity": "JCB", "source": "S", "days_ago": 4,
         "url": "https://siteF.test/6"},
        {"title": "JCB raises funding round of 100 crore from investors - Daily", "entity": "JCB", "source": "S",
         "days_ago": 4, "url": "https://siteG.test/7"},
        {"title": "JCB appoints a new chief executive officer", "entity": "JCB", "source": "S", "days_ago": 100,
         "url": "https://siteH.test/8"},
        {"title": "ISM chips update", "summary": "Nothing here.", "entity": "ISM", "source": "S", "days_ago": 1,
         "url": "https://siteI.test/9"},
    ])


def _get(client, path, **params):
    r = client.get(path, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_client_view_contains_the_whole_graph(admin_client, db_engine) -> None:
    _corpus(db_engine)
    v = _get(admin_client, "/intelligence/clients/JCB/view")
    assert v["query"] == "client:jcb" and v["source_result_count"] == 8               # ISM article excluded
    assert {e["canonical_key"] for e in v["entities"]} == {"jcb"}
    assert len(v["events"]) == 6 and v["raw_event_count"] == 8                        # 2 pairs of same-story headlines merged (launch, funding)
    assert len(v["relationships"]) == 6 and len(v["timeline"]) == 6
    assert v["insights"] and v["opportunities"] and v["alerts"]
    assert {a["client"] for a in v["articles"]} == {"JCB"}
    assert all(ev["provider"] == "stored_articles" for e in v["events"] for ev in e["evidence"])


def test_client_name_is_case_insensitive_and_unknown_client_is_404(admin_client, db_engine) -> None:
    _corpus(db_engine)
    assert _get(admin_client, "/intelligence/clients/jcb/view")["query"] == "client:jcb"
    assert admin_client.get("/intelligence/clients/NOPE/view").status_code == 404
    assert admin_client.get("/intelligence/clients/NOPE/briefing").status_code == 404


def test_insights_are_rule_based_and_traceable(admin_client, db_engine) -> None:
    _corpus(db_engine)
    v = _get(admin_client, "/intelligence/clients/JCB/view")
    by_rule = {}
    for i in v["insights"]:
        by_rule.setdefault(i["rule_id"], []).append(i)
    cluster = by_rule["I2_activity_cluster"][0]
    assert cluster["insight_type"] == "activity_cluster" and cluster["client_key"] == "jcb"
    assert cluster["basis"]["event_type"] == "launch" and cluster["basis"]["events"] == 4
    assert "not merged as the same event" in cluster["explanation"] and "Rule I2" in cluster["explanation"]
    multi = by_rule["I1_multi_source_event"]
    assert {m["basis"]["event_type"] for m in multi} == {"launch", "funding"}
    assert all(len(m["basis"]["publishers"]) == 2 for m in multi)
    events = {e["event_key"] for e in v["events"]}
    articles = {a["article_id"] for a in v["articles"]}
    for i in v["insights"]:
        assert set(i["event_keys"]) <= events and set(i["article_ids"]) <= articles and i["evidence"]
        assert 0 <= i["strength"] <= 1 and "business_impact" in i["unavailable"]
    assert [i["strength"] for i in v["insights"]] == sorted((i["strength"] for i in v["insights"]), reverse=True)


def test_new_activity_type_needs_a_baseline(admin_client, db_engine) -> None:
    _seed(db_engine, [
        {"title": "JCB launches backhoe model one for farms", "entity": "JCB", "source": "S", "days_ago": 90, "url": "https://a1.test/1"},
        {"title": "JCB unveils excavator model two for mining", "entity": "JCB", "source": "S", "days_ago": 80, "url": "https://a2.test/2"},
        {"title": "JCB raises funding round of 100 crore from investors", "entity": "JCB", "source": "S", "days_ago": 1,
         "url": "https://a3.test/3"},
    ])
    v = _get(admin_client, "/intelligence/clients/JCB/view")
    new = [i for i in v["insights"] if i["rule_id"] == "I3_new_activity_type"]
    assert len(new) == 1 and new[0]["basis"]["new_type"] == "funding" and new[0]["basis"]["baseline_events"] == 2
    assert "not in the stored corpus is unknown" in new[0]["explanation"]
    only = _get(admin_client, "/intelligence/clients/JCB/view", limit=1)              # no baseline -> no claim of novelty
    assert not [i for i in only["insights"] if i["rule_id"] == "I3_new_activity_type"]


def test_opportunities_come_from_events_and_never_claim_a_capability(admin_client, db_engine) -> None:
    _corpus(db_engine)
    ops = _get(admin_client, "/intelligence/clients/JCB/opportunities")
    kinds = {o["rule_id"] for o in ops}
    assert "O_funding" in kinds and "O_appointment" in kinds
    funding = next(o for o in ops if o["rule_id"] == "O_funding")
    assert funding["opportunity_score"] == 0.9 and funding["score_components"]["multi_publisher_bonus"] == 0.1
    assert funding["status"] == "POTENTIAL" and funding["capability_match"] == "UNVERIFIED" and funding["capability"] is None
    assert "not a statement of the client's needs" in funding["interpretation"]
    assert funding["evidence"] and funding["article_ids"] and funding["insight_ids"]
    launch_titles = [o["title"] for o in ops if o["rule_id"] == "O_launch"]
    assert len(launch_titles) == 1 and "52 tonne" in launch_titles[0]                # single-publisher launches: below threshold
    assert all(o["opportunity_score"] >= 0.4 for o in ops)


def test_alerts_are_rule_and_window_based(admin_client, db_engine) -> None:
    _corpus(db_engine)
    alerts = _get(admin_client, "/intelligence/clients/JCB/alerts")
    by_rule = {a["rule_id"]: a for a in alerts}
    assert by_rule["A1_capital_or_control_event"]["severity"] == "critical"          # funding from 2 publishers
    assert by_rule["A1_capital_or_control_event"]["triggered_because"]["publishers"] == 2
    assert not [a for a in alerts if a["rule_id"] == "A2_leadership_change"]          # appointment is 100 days old
    assert all(a["evidence"] and a["event_keys"] for a in alerts)
    assert [a["severity"] for a in alerts] == sorted((a["severity"] for a in alerts), key=["critical", "warning", "info"].index)
    late = _get(admin_client, "/intelligence/clients/JCB/alerts", as_of="2026-12-31T00:00:00Z")
    assert late == []                                                                 # everything is outside the window
    early = _get(admin_client, "/intelligence/clients/JCB/alerts", as_of="2026-06-01T00:00:00Z")
    assert early == [] or all(a["as_of"].startswith("2026-06-01") for a in early)     # explicit as_of is honoured, no wall clock


def test_briefing_is_a_coherent_traceable_summary(admin_client, db_engine) -> None:
    _corpus(db_engine)
    b = _get(admin_client, "/intelligence/clients/JCB/briefing")
    assert b["scope"] == {"client": "JCB"} and b["as_of_basis"] == "latest stored event date"
    assert b["generated_from"] == {"articles": 8, "events": 6, "entities": 1, "relationships": 6}
    assert "8 stored articles" in b["summary"] and "6 distinct events" in b["summary"]
    whens = [e["when"] for e in b["key_events"]]
    assert whens == sorted(whens, reverse=True)                                       # latest first
    assert all(e["evidence"] and e["article_ids"] for e in b["key_events"])
    assert len(b["timeline"]) == 6 and {p["publisher"] for p in b["sources"] if "publisher" in p} >= {"sited.test", "sitee.test"}
    assert b["insights"] and b["opportunities"] and b["alerts"]
    assert any("publication_fallback" in x for x in b["limitations"])
    assert any("latest event date in the stored corpus" in x for x in b["limitations"])
    explicit = _get(admin_client, "/intelligence/clients/JCB/briefing", as_of="2026-09-01T00:00:00Z")
    assert explicit["as_of_basis"] == "explicit"


def test_topic_briefing_uses_the_ranked_search(admin_client, db_engine) -> None:
    _corpus(db_engine)
    b = _get(admin_client, "/intelligence/briefing", q="excavator launch")
    assert b["scope"] == {"query": "excavator launch"} and b["generated_from"]["articles"] >= 2
    assert admin_client.get("/intelligence/briefing", params={"q": "  "}).status_code == 422


def test_everything_is_deterministic(admin_client, db_engine) -> None:
    _corpus(db_engine)
    a = _get(admin_client, "/intelligence/clients/JCB/view")
    assert a == _get(admin_client, "/intelligence/clients/JCB/view")
    assert _get(admin_client, "/intelligence/clients/JCB/briefing") == _get(admin_client, "/intelligence/clients/JCB/briefing")


def test_research_extract_carries_the_same_derived_objects(admin_client, db_engine) -> None:
    _corpus(db_engine)
    body = admin_client.post("/intelligence/extract", json={"query": "JCB launch funding appoints raises", "limit": 20}).json()
    assert {"insights", "opportunities", "alerts"} <= set(body)


# ---- temporal intelligence ---------------------------------------------------------------------------------------

def _set_body(db_engine, article_id, raw_text):
    async def run():
        async with db_engine.begin() as c:
            await c.execute(text("update articles set raw_text=:r where id=:i"), {"r": raw_text, "i": article_id})
    asyncio.run(run())


def test_stated_date_becomes_event_time_and_fallback_is_never_relabelled(admin_client, db_engine) -> None:
    ids = _seed(db_engine, [
        {"title": "Trade show notes", "summary": "Visitors.", "entity": "JCB", "source": "S", "days_ago": 1, "url": "https://x1.test/1"},
        {"title": "JCB launches undated model delta for farms", "entity": "JCB", "source": "S", "days_ago": 2, "url": "https://x2.test/2"},
    ])
    _set_body(db_engine, ids["Trade show notes"], "JCB unveiled a backhoe at the expo on 25 August 2026, the firm said.")
    v = _get(admin_client, "/intelligence/clients/JCB/view")
    dated = next(e for e in v["events"] if e["event_time"])
    undated = next(e for e in v["events"] if not e["event_time"])
    assert dated["event_time"].startswith("2026-08-25") and dated["event_time_source"] == "article_text"
    assert dated["event_time_precision"] == "day" and "25 August 2026" in dated["evidence"][0]["content_span"]
    assert undated["event_time_source"] == "unknown" and undated["event_time_precision"] == "unknown"
    tl = {t["event_key"]: t for t in v["timeline"]}
    assert tl[dated["event_key"]]["ordering_time_source"] == "event_time"
    assert tl[undated["event_key"]]["ordering_time_source"] == "publication_fallback"
    assert tl[dated["event_key"]]["position"] == 1                                    # 25 Aug precedes the 30 Aug publication


# ---- traceability audit ------------------------------------------------------------------------------------------

def _audit(db_engine, mutate=None):
    async def run():
        async with AsyncSession(db_engine) as s:
            resp = await client_intelligence(s, "JCB", limit=500)
            if mutate:
                mutate(resp)
            return await audit_response(s, resp)
    return asyncio.run(run())


def test_audit_passes_for_the_real_pipeline(admin_client, db_engine) -> None:
    _corpus(db_engine)
    report = _audit(db_engine)
    assert report["violations"] == [] and report["evidence"] > 0 and report["references"] > 0
    assert report["insights"] and report["opportunities"] and report["alerts"]


def test_audit_detects_fabricated_evidence_and_dangling_references(admin_client, db_engine) -> None:
    _corpus(db_engine)

    def tamper(resp):
        resp.insights[0].evidence[0].content_span = "JCB acquired the moon."
        resp.opportunities[0].event_keys.append("ev-doesnotexist")
        resp.alerts[0].insight_ids.append("in-doesnotexist")

    report = _audit(db_engine, tamper)
    text_ = " | ".join(report["violations"])
    assert "span not found" in text_ and "unknown event ev-doesnotexist" in text_ and "unknown insight in-doesnotexist" in text_


# ---- briefing persistence (the only write path) ------------------------------------------------------------------

def test_persist_briefing_is_idempotent_and_listed(admin_client, db_engine) -> None:
    _corpus(db_engine)
    first = admin_client.post("/intelligence/clients/JCB/briefing/persist")
    second = admin_client.post("/intelligence/clients/JCB/briefing/persist")
    assert first.status_code == second.status_code == 200, first.text
    assert first.json()["id"] == second.json()["id"]                                  # same scope + date: replaced, not duplicated
    assert first.json()["payload"]["scope"] == {"client": "JCB"} and first.json()["payload"]["insights"]
    listed = admin_client.get("/intelligence/briefings").json()
    assert [b["title"] for b in listed] == ["Briefing: JCB"]
    async def cleanup():
        async with db_engine.begin() as c:
            await c.execute(text("delete from briefings"))
    asyncio.run(cleanup())


def test_persist_rejected_for_viewer_and_unknown_client(viewer_client) -> None:
    assert viewer_client.post("/intelligence/clients/JCB/briefing/persist").status_code == 403
    assert viewer_client.get("/intelligence/clients/JCB/view").status_code == 200


def test_anonymous_rejected(anon_client) -> None:
    assert anon_client.get("/intelligence/clients/JCB/view").status_code in (401, 403)
    assert anon_client.get("/intelligence/briefing", params={"q": "x"}).status_code in (401, 403)


def test_aggregator_urls_do_not_hide_distinct_publishers(admin_client, db_engine) -> None:
    """Google News wrapper URLs share one host; the stored publisher_url decides how many publishers report an event."""
    _seed(db_engine, [
        {"title": "JCB raises funding round of 100 crore from investors", "entity": "JCB", "source": "S", "days_ago": 1,
         "url": "https://news.google.com/rss/articles/AAA", "publisher_url": "https://economictimes.test/a"},
        {"title": "JCB raises funding round of 100 crore from investors - Mint", "entity": "JCB", "source": "S", "days_ago": 1,
         "url": "https://news.google.com/rss/articles/BBB", "publisher_url": "https://livemint.test/b"},
        {"title": "JCB unveils excavator model omega for mining sites", "entity": "JCB", "source": "S", "days_ago": 2,
         "url": "https://news.google.com/rss/articles/CCC", "publisher_url": "https://same.test/c"},
        {"title": "JCB unveils excavator model omega for mining sites - Feed", "entity": "JCB", "source": "S", "days_ago": 2,
         "url": "https://news.google.com/rss/articles/DDD", "publisher_url": "https://same.test/d"},
    ])
    v = _get(admin_client, "/intelligence/clients/JCB/view")
    multi = {i["basis"]["event_type"]: i for i in v["insights"] if i["rule_id"] == "I1_multi_source_event"}
    assert sorted(multi["funding"]["basis"]["publishers"]) == ["economictimes.test", "livemint.test"]
    assert "launch" not in multi                                                    # same publisher twice is one publisher
    b = _get(admin_client, "/intelligence/clients/JCB/briefing")
    assert {p["publisher"] for p in b["sources"] if "publisher" in p} >= {"economictimes.test", "livemint.test", "same.test"}


def test_opportunities_carry_a_review_only_recommended_action(admin_client, db_engine) -> None:
    _corpus(db_engine)
    ops = _get(admin_client, "/intelligence/clients/JCB/opportunities")
    assert ops and all(o["review_status"] == "needs_review" for o in ops)
    funding = next(o for o in ops if o["rule_id"] == "O_funding")
    assert funding["recommended_action"].startswith("Verify the funding event in the cited source, then ")
    assert funding["action_basis"] == f"O_funding on event {funding['event_keys'][0]}"
    assert all(o["recommended_action"].startswith("Verify the ") for o in ops)          # every action starts with verification
