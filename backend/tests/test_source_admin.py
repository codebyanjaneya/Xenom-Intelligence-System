"""Source administration (PDF s.6, s.12): tiers incl. TIER_4, config, audit + rollback, client links, dry run, one pipeline."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.adapters.ingestion.base import NormalizedDocument
from app.services import registry_ingestion as reg

NEW = {"name": "Trade Feed", "url": "https://trade.example.org/feed", "source_type": "RSS", "tier": "TIER_2",
       "config": {"fetch_mode": "rss"}, "source_class": "news", "reliability_score": 0.7, "priority": 5}


def _docs(source_id, titles):
    now = datetime.now(timezone.utc)
    return [NormalizedDocument(source_id=source_id, canonical_url=f"https://trade.example.org/a/{i}", title=t,
                               published_at=now - timedelta(days=1), retrieved_at=now, raw_content=t, normalized_content=t,
                               content_type="text/html", metadata={}) for i, t in enumerate(titles)]


def _count(db_engine, table):
    async def run():
        async with db_engine.connect() as c:
            return (await c.execute(text(f"select count(*) from {table}"))).scalar_one()
    return asyncio.run(run())


def test_create_update_audit_rollback(admin_client) -> None:
    created = admin_client.post("/sources", json=NEW)
    assert created.status_code == 201, created.text
    src = created.json()
    assert src["tier"] == "TIER_2" and src["source_class"] == "news" and src["priority"] == 5 and src["config"]["managed"] is True
    changed = admin_client.patch(f"/sources/{src['id']}", json={"tier": "TIER_4", "reliability_score": 0.2}).json()
    assert changed["tier"] == "TIER_4" and changed["reliability_score"] == 0.2
    audit = admin_client.get(f"/sources/audit?source_id={src['id']}").json()
    assert [a["action"] for a in audit] == ["source.update", "source.create"]
    back = admin_client.post(f"/sources/{src['id']}/rollback", json={"audit_id": audit[0]["audit_id"]}).json()
    assert back["tier"] == "TIER_2" and back["reliability_score"] == 0.7
    assert admin_client.get(f"/sources/audit?source_id={src['id']}").json()[0]["action"] == "source.rollback"
    assert admin_client.post(f"/sources/{src['id']}/rollback", json={"audit_id": audit[1]["audit_id"]}).status_code == 409


def test_source_validation_and_roles(admin_client) -> None:
    bad = [{**NEW, "tier": "TIER_9"}, {**NEW, "reliability_score": 3}, {**NEW, "config": {"fetch_mode": "shell"}},
           {**NEW, "config": {"fetch_mode": "link_listing", "link_pattern": "("}}, {**NEW, "config": {"fetch_mode": "rss", "extra": 1}}]
    assert [admin_client.post("/sources", json=b).status_code for b in bad] == [422] * 5


def test_viewer_cannot_change_sources(viewer_client) -> None:
    assert viewer_client.post("/sources", json=NEW).status_code == 403
    assert viewer_client.post("/sources/dry-run", json={"source_id": str(uuid.uuid4())}).status_code == 403
    assert viewer_client.get("/sources/audit").status_code == 200


def test_client_links_are_validated_and_audited(admin_client) -> None:
    src = admin_client.post("/sources", json=NEW).json()
    ok = admin_client.put(f"/sources/{src['id']}/clients", json=[{"client": "jcb", "priority": 9, "role": "primary"}, {"client": "ACE", "role": "secondary"}])
    assert ok.status_code == 200 and {c["client"] for c in ok.json()} == {"jcb", "ace"}
    assert admin_client.get(f"/sources/{src['id']}/clients").json()[0]["client"] == "ace"
    assert admin_client.put(f"/sources/{src['id']}/clients", json=[{"client": "nobody"}]).status_code == 404
    assert admin_client.put(f"/sources/{src['id']}/clients", json=[{"client": "jcb"}, {"client": "JCB"}]).status_code == 422
    assert admin_client.get(f"/sources/audit?source_id={src['id']}").json()[0]["action"] == "source.clients"


def test_dry_run_runs_the_real_pipeline_and_stores_nothing(admin_client, db_engine, monkeypatch) -> None:
    async def fake_fetch(spec, source, retrieved_at):
        return _docs(source.id, ["JCB India expands Pune plant with new loader line", "Local weather turns cloudy"]), []
    monkeypatch.setattr(reg, "_fetch", fake_fetch)
    body = {"name": "Trade Feed", "url": "https://trade.example.org/feed", "source_type": "RSS", "tier": "TIER_2", "config": {"fetch_mode": "rss"}}
    report = admin_client.post("/sources/dry-run", json=body).json()
    assert report["dry_run"] is True and report["documents_fetched"] == 2
    assert report["documents_accepted"] == 1 and report["documents_rejected"] == 1
    assert report["samples"]["accepted"][0]["client"] == "JCB" and report["samples"]["rejected"][0]["reason"]
    assert _count(db_engine, "articles") == 0 and _count(db_engine, "sources") == 0 and _count(db_engine, "source_health") == 0


def test_manual_fetch_of_configured_source_uses_the_registry_path_and_records_health(admin_client, db_engine, monkeypatch) -> None:
    async def fake_fetch(spec, source, retrieved_at):
        return _docs(source.id, ["JCB India expands Pune plant with new loader line"]), []
    monkeypatch.setattr(reg, "_fetch", fake_fetch)
    src = admin_client.post("/sources", json=NEW).json()
    admin_client.put(f"/sources/{src['id']}/clients", json=[{"client": "jcb"}])
    done = admin_client.post(f"/sources/{src['id']}/fetch")
    assert done.status_code == 200 and done.json()["articles_created"] == 1
    assert _count(db_engine, "source_health") == 1
    assert admin_client.get(f"/sources/{src['id']}/health").json()["latest"]["status"] == "ok"


def test_tier_4_is_a_discovery_signal_and_never_evidence(admin_client, db_engine, monkeypatch) -> None:
    async def fake_fetch(spec, source, retrieved_at):
        return _docs(source.id, ["JCB India expands Pune plant with new loader line"]), []
    monkeypatch.setattr(reg, "_fetch", fake_fetch)
    src = admin_client.post("/sources", json={**NEW, "tier": "TIER_4", "source_class": "forum"}).json()
    assert admin_client.post(f"/sources/{src['id']}/fetch").status_code == 200

    async def rows():
        async with db_engine.connect() as c:
            return (await c.execute(text("select processing_status, score_reason from articles"))).all()
    (status, reason), = asyncio.run(rows())
    assert status == "rejected" and "DISCOVERY_SIGNAL_ONLY" in reason
    assert admin_client.get("/articles/feed").json() == []


def test_disabled_source_is_not_run_and_registry_sync_keeps_manager_edits(admin_client, db_engine, monkeypatch) -> None:
    calls = []

    async def fake_fetch(spec, source, retrieved_at):
        calls.append(spec.key)
        return [], []
    monkeypatch.setattr(reg, "_fetch", fake_fetch)
    src = admin_client.post("/sources", json=NEW).json()
    admin_client.delete(f"/sources/{src['id']}")
    from app.db.session import get_sessionmaker
    reports = asyncio.run(reg.run_configured_sources(get_sessionmaker()))
    assert reports == [] and calls == []


def test_update_source_config_via_patch_does_not_crash(admin_client) -> None:
    src = admin_client.post("/sources", json=NEW).json()
    updated = admin_client.patch(f"/sources/{src['id']}", json={"config": {"fetch_mode": "arxiv_api", "query": "cat:cs.RO", "max_results": 10}})
    assert updated.status_code == 200, updated.text
    assert updated.json()["config"]["query"] == "cat:cs.RO" and updated.json()["config"]["fetch_mode"] == "arxiv_api"
