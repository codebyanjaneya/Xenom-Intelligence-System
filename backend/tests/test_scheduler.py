"""Continuous-update path: scheduler config switch, per-cycle coverage of all clients, error isolation, status,
and health recording for every fetch attempt. No network: adapters and runners are replaced by fakes."""
from __future__ import annotations

import asyncio
from urllib.parse import quote_plus

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config.clients import get_all_clients
from app.config.scheduler import SCHEDULER_CONFIG, SchedulerConfig
from app.config.source_registry import SOURCE_REGISTRY
from app.services import background_scheduler as bs
from app.services import registry_ingestion as ri


@pytest.mark.parametrize("value,expected", [("false", False), ("0", False), ("off", False), ("No", False),
                                            ("true", True), ("1", True), ("", True)])
def test_scheduler_env_switch(monkeypatch, value, expected) -> None:
    monkeypatch.setenv("SCHEDULER_ENABLED", value)
    assert SchedulerConfig().enabled is expected


def test_scheduler_defaults_to_enabled_without_env(monkeypatch) -> None:
    monkeypatch.delenv("SCHEDULER_ENABLED", raising=False)
    assert SchedulerConfig().enabled is True and SchedulerConfig().interval_minutes == 10


def _report(fetched=3, accepted=1, rejected=2):
    return {"fetch_status": "ok", "documents_fetched": fetched, "documents_parsed": fetched, "documents_deduplicated": 0,
            "documents_accepted": accepted, "documents_rejected": rejected, "adapter_errors": []}


def test_cycle_covers_every_client_query_with_registry_urls_and_isolates_failures(monkeypatch) -> None:
    calls = []

    async def fake_google(maker, client, query, url, **kw):
        calls.append((client, query, url))
        if client == "ACE" and len([c for c in calls if c[0] == "ACE"]) == 1:
            raise RuntimeError("boom")                                     # one query crashes
        if client == "VIPS" and len([c for c in calls if c[0] == "VIPS"]) == 1:
            return {"fetch_status": "failed", "fetch_error": "HTTPError: 503"}
        return _report()

    async def fake_registry(maker, **kw):
        return [{"key": "reg:ok", "fetch_status": "ok", "documents_accepted": 4, "documents_rejected": 1},
                {"key": "reg:bad", "fetch_status": "failed", "fetch_error": "TimeoutError"}]

    monkeypatch.setattr(bs, "get_sessionmaker", lambda: object())
    monkeypatch.setattr(bs, "run_google_query", fake_google)
    monkeypatch.setattr(bs, "run_registry", fake_registry)
    monkeypatch.setattr(SCHEDULER_CONFIG, "registry_enabled", True)
    monkeypatch.setattr(SCHEDULER_CONFIG, "theme_discovery_enabled", False)      # theme cadence is tested in test_closure
    summary = asyncio.run(bs.run_scheduled_pipeline())

    specs = ri.google_specs()
    assert [(c, q, u) for c, q, u in calls] == [(c, q, u) for c, q, u in specs]       # every configured query, in order
    assert all(u == f"https://news.google.com/search?q={quote_plus(q)}" for _c, q, u in calls)   # registry URL form
    assert set(summary["by_client"]) == set(get_all_clients()) and summary["clients_processed"] == 6
    failed = " | ".join(summary["failed_sources"])
    assert "ACE/" in failed and "boom" in failed                                        # crash recorded, cycle continued
    assert "VIPS/" in failed and "HTTPError: 503" in failed
    assert "reg:bad: TimeoutError" in failed and summary["registry_sources_run"] == 1
    ok_queries = len(specs) - 2
    assert summary["total_fetched"] == 3 * ok_queries
    assert summary["total_accepted"] == 1 * ok_queries + 4 and summary["total_rejected"] == 2 * ok_queries + 1
    assert summary["by_client"]["JCB"]["total_fetched"] == 3 * len([1 for c, *_ in specs if c == "JCB"])


def test_registry_failure_does_not_lose_the_google_results(monkeypatch) -> None:
    async def fake_registry(maker, **kw):
        raise RuntimeError("registry down")

    async def fake_google(maker, client, query, url, **kw):
        return _report()

    monkeypatch.setattr(bs, "get_sessionmaker", lambda: object())
    monkeypatch.setattr(bs, "run_google_query", fake_google)
    monkeypatch.setattr(bs, "run_registry", fake_registry)
    monkeypatch.setattr(SCHEDULER_CONFIG, "registry_enabled", True)
    monkeypatch.setattr(SCHEDULER_CONFIG, "theme_discovery_enabled", False)
    summary = asyncio.run(bs.run_scheduled_pipeline())
    assert summary["total_accepted"] == len(ri.google_specs()) and "registry: registry down" in summary["failed_sources"]


def test_scheduler_status_and_loop_record_the_last_run(monkeypatch) -> None:
    runner = bs.SchedulerRunner()
    assert runner.status()["last_summary"] is None and runner.status()["interval_minutes"] == 10

    async def fake_run():
        return {"total_accepted": 7}

    async def fake_sleep(seconds):
        runner.running = False

    monkeypatch.setattr(bs, "run_scheduled_pipeline", fake_run)
    monkeypatch.setattr(bs.asyncio, "sleep", fake_sleep)
    runner.running = True
    asyncio.run(runner._scheduler_loop())
    st = runner.status()
    assert st["last_summary"]["total_accepted"] == 7 and st["last_started_at"]


def test_disabled_scheduler_does_not_start(monkeypatch) -> None:
    monkeypatch.setattr(SCHEDULER_CONFIG, "enabled", False)
    runner = bs.SchedulerRunner()
    asyncio.run(runner.start())
    assert runner.running is False and runner.status()["enabled"] is False


# ---- health recording ------------------------------------------------------------------------------------------------

def _health(db_engine):
    async def run():
        async with db_engine.connect() as c:
            return (await c.execute(text("select s.name, h.status, h.error_message, h.latency_ms from source_health h "
                                         "join sources s on s.id=h.source_id order by h.checked_at"))).all()
    return asyncio.run(run())


def test_google_query_records_ok_error_and_timeout(admin_client, db_engine, monkeypatch) -> None:
    maker = async_sessionmaker(db_engine, expire_on_commit=False)
    client, query, url = ri.google_specs()[0]
    outcomes = iter([[], RuntimeError("HTTP 503 from feed"), asyncio.TimeoutError()])

    async def fake_fetch(self, **kw):
        out = next(outcomes)
        if isinstance(out, BaseException):
            raise out
        return out

    monkeypatch.setattr(ri.GoogleNewsAdapter, "fetch", fake_fetch)
    reports = [asyncio.run(ri.run_google_query(maker, client, query, url)) for _ in range(3)]
    assert [r["fetch_status"] for r in reports] == ["ok", "failed", "failed"]
    rows = _health(db_engine)
    assert [r[1] for r in rows] == ["ok", "error", "timeout"] and all(r[0].startswith("Google News:") for r in rows)
    assert "HTTP 503" in rows[1][2] and rows[0][2] is None and all(r[3] >= 0 for r in rows)


def test_registry_spec_records_health_for_success_and_failure(admin_client, db_engine, monkeypatch) -> None:
    maker = async_sessionmaker(db_engine, expire_on_commit=False)
    spec = next(s for s in SOURCE_REGISTRY if s.enabled and s.fetch_mode == "rss")
    state = {"fail": False}

    async def fake_fetch(spec_, source, retrieved_at):
        if state["fail"]:
            raise ri.AdapterError("feed unreachable")
        return [], []

    monkeypatch.setattr(ri, "_fetch", fake_fetch)
    ok = asyncio.run(ri.run_spec(maker, spec))
    state["fail"] = True
    bad = asyncio.run(ri.run_spec(maker, spec))
    assert ok["fetch_status"] == "ok" and bad["fetch_status"] == "failed"
    assert [(r[1], r[2]) for r in _health(db_engine)] == [("ok", None), ("error", "AdapterError: feed unreachable")]


def test_reference_only_sources_are_skipped_without_a_health_row(admin_client, db_engine) -> None:
    maker = async_sessionmaker(db_engine, expire_on_commit=False)
    spec = next(s for s in SOURCE_REGISTRY if not s.enabled)
    report = asyncio.run(ri.run_spec(maker, spec))
    assert report["fetch_status"].startswith("skipped") and _health(db_engine) == []


def test_record_health_truncates_and_maps_status(admin_client, db_engine) -> None:
    async def run():
        async with db_engine.begin() as c:
            await c.execute(text("insert into sources (id,name,canonical_url,source_type,tier,is_active) values "
                                 "(gen_random_uuid(),'H src','https://h.test/f','RSS','TIER_2',true)"))
        maker = async_sessionmaker(db_engine, expire_on_commit=False)
        async with maker() as s:
            sid = (await s.execute(text("select id from sources where name='H src'"))).scalar()
            await ri.record_health(s, sid, seconds=1.5, error="x" * 900)
            await ri.record_health(s, sid, seconds=0.2, error="slow", timeout=True)
    asyncio.run(run())
    rows = _health(db_engine)
    assert [r[1] for r in rows] == ["error", "timeout"] and len(rows[0][2]) == 500 and rows[0][3] == 1500
