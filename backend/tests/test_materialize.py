"""Rule-derived intelligence persisted for review: insights (draft), opportunities, alerts; audited back to stored articles."""
from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.trace_audit import audit_architecture
from tests.test_client_intelligence import _corpus


def _q(db_engine, sql, **params):
    async def run():
        async with db_engine.connect() as c:
            return (await c.execute(text(sql), params)).all()
    return asyncio.run(run())


def test_materialize_persists_drafts_idempotently_and_keeps_human_decisions(admin_client, db_engine) -> None:
    _corpus(db_engine)
    first = admin_client.post("/engine/materialize", json={"client": "JCB"}).json()[0]
    assert first["insights_created"] >= 1 and first["opportunities_created"] >= 1
    insights = admin_client.get("/engine/insights?client=jcb").json()
    assert insights and {i["status"] for i in insights} == {"draft"} and "Evidence:" in insights[0]["body"] and insights[0]["client"] == "JCB"
    again = admin_client.post("/engine/materialize", json={"client": "JCB"}).json()[0]
    assert again["insights_created"] == 0 and again["opportunities_created"] == 0 and again["alerts_created"] == 0
    counts = _q(db_engine, "select (select count(*) from insights), (select count(*) from opportunities), (select count(*) from alerts)")[0]
    assert counts[0] == first["insights_created"] and counts[1] == first["opportunities_created"]
    target = insights[0]["id"]
    assert admin_client.patch(f"/engine/insights/{target}", json={"status": "published"}).json()["status"] == "published"
    admin_client.post("/engine/materialize", json={"client": "JCB"})
    assert next(i for i in admin_client.get("/engine/insights?client=jcb").json() if i["id"] == target)["status"] == "published"
    assert admin_client.get("/engine/insights?status=archived").json() == []


def test_materialized_rows_pass_the_trace_audit_and_wrappers_are_caught(admin_client, db_engine) -> None:
    _corpus(db_engine)
    admin_client.post("/engine/materialize", json={})

    async def audit():
        async with AsyncSession(db_engine) as s:
            return await audit_architecture(s)
    assert [v for v in asyncio.run(audit())["violations"] if v.startswith(("insights", "opportunities", "alerts"))] == []

    async def tamper():
        async with db_engine.begin() as c:
            await c.execute(text("update insights set body = replace(body, 'http', 'https://news.google.com/rss/articles/x http')"))
    asyncio.run(tamper())
    assert any("discovery wrapper" in v or "not a stored article" in v for v in asyncio.run(audit())["violations"])


def test_materialize_validates_clients(admin_client) -> None:
    assert admin_client.post("/engine/materialize", json={"client": "nobody"}).status_code == 404


def test_materialize_is_admin_only(viewer_client) -> None:
    assert viewer_client.post("/engine/materialize", json={}).status_code == 403
    assert viewer_client.get("/engine/insights").status_code == 200
