"""Versioned client profiles: save/rollback/audit, loader over the rule engine, impact preview, suggest-then-confirm."""
from __future__ import annotations

import time

from app.services import profiles as prof
from app.services.client_relevance import evaluate_relevance
from tests.test_article_search import _seed


def _profile(admin_client, client="JCB"):
    return admin_client.get(f"/profiles/{client}").json()


def test_client_without_saved_profile_falls_back_to_code(admin_client) -> None:
    body = _profile(admin_client)
    assert body["origin"] == "code" and body["version"] == 0
    assert "JCB India" in body["data"]["aliases"] and body["versions"] == []


def test_save_creates_new_active_version_and_audit(admin_client) -> None:
    data = _profile(admin_client)["data"]
    data["competitors"] = ["Caterpillar"]
    saved = admin_client.put("/profiles/JCB", json={"data": data, "note": "add competitor"}).json()
    assert saved["version"] == 1 and saved["origin"] == "saved" and saved["data"]["competitors"] == ["Caterpillar"]
    data["competitors"] = ["Caterpillar", "Komatsu"]
    assert admin_client.put("/profiles/JCB", json={"data": data}).json()["version"] == 2
    versions = _profile(admin_client)["versions"]
    assert [v["version"] for v in versions] == [2, 1] and [v["is_active"] for v in versions] == [True, False]
    audit = admin_client.get("/profiles/audit").json()
    assert audit[0]["action"] == "profile.save" and audit[0]["changes"]["competitors"]["added"] == ["Komatsu"]


def test_rollback_recreates_old_version_as_new(admin_client) -> None:
    data = _profile(admin_client)["data"]
    admin_client.put("/profiles/JCB", json={"data": {**data, "competitors": ["A"]}})
    admin_client.put("/profiles/JCB", json={"data": {**data, "competitors": ["B"]}})
    back = admin_client.post("/profiles/JCB/rollback", json={"version": 1}).json()
    assert back["version"] == 3 and back["data"]["competitors"] == ["A"]
    assert back["versions"][0]["reverted_from"] == 1
    assert admin_client.post("/profiles/JCB/rollback", json={"version": 99}).status_code == 404


def test_saved_profile_drives_the_rule_engine_and_fallback_restores(admin_client) -> None:
    title = "Bamfordco unveils new excavator range"
    assert not evaluate_relevance("JCB", title=title, body="").accepted
    data = _profile(admin_client)["data"]
    data["strong_keywords"] = [*data["strong_keywords"], "Bamfordco"]
    admin_client.put("/profiles/JCB", json={"data": data})
    from app.config.entity_config import ENTITY_CONFIGS
    assert "Bamfordco" in ENTITY_CONFIGS["JCB"].strong_keywords
    assert evaluate_relevance("JCB", title=title, body="").accepted
    prof.apply_overlay("JCB", None)
    assert not evaluate_relevance("JCB", title=title, body="").accepted


def test_profile_validation(admin_client) -> None:
    data = _profile(admin_client)["data"]
    assert admin_client.put("/profiles/JCB", json={"data": {**data, "surprise": 1}}).status_code == 422
    assert admin_client.put("/profiles/JCB", json={"data": {**data, "aliases": ["x" * 500]}}).status_code == 422
    assert admin_client.put("/profiles/JCB", json={"data": {**data, "source_priority": {"a.com": 99}}}).status_code == 422
    assert admin_client.get("/profiles/NOPE").status_code == 404


def test_viewer_can_read_but_not_write_profiles(viewer_client) -> None:
    data = viewer_client.get("/profiles/JCB").json()["data"]
    assert viewer_client.put("/profiles/JCB", json={"data": data}).status_code == 403
    assert viewer_client.post("/profiles/JCB/rollback", json={"version": 1}).status_code == 403
    assert viewer_client.post("/profiles/JCB/reevaluate").status_code == 403
    assert viewer_client.post("/profiles/JCB/suggestions/generate").status_code == 403


def test_anonymous_cannot_touch_profiles(anon_client) -> None:
    assert anon_client.get("/profiles/JCB").status_code == 401
    assert anon_client.put("/profiles/JCB", json={"data": {}}).status_code == 401


def test_impact_preview_counts_stored_articles_without_saving(admin_client, db_engine) -> None:
    _seed(db_engine, [
        {"title": "Bamfordco launches new loader", "summary": "", "entity": "ISM", "status": "rejected", "gate": False, "source": "S"},
        {"title": "JCB India expands Pune plant", "summary": "", "entity": "JCB", "source": "S"},
    ])
    data = _profile(admin_client)["data"]
    data["strong_keywords"] = [*data["strong_keywords"], "Bamfordco"]
    preview = admin_client.post("/profiles/JCB/preview", json={"data": data}).json()
    assert preview["would_add"] == 1 and preview["would_remove"] == 0 and preview["evaluated_articles"] == 2
    assert preview["added_examples"][0]["title"] == "Bamfordco launches new loader"
    assert _profile(admin_client)["version"] == 0  # nothing saved


def _wait(admin_client, job):
    for _ in range(60):
        status = admin_client.get(f"/profiles/JCB/reevaluate/{job['job_id']}").json()
        if status["status"] != "running":
            return status
        time.sleep(0.5)
    return status


def test_reevaluation_only_applies_what_the_profile_change_decides(admin_client, db_engine) -> None:
    _seed(db_engine, [{"title": "Bamfordco launches new loader in Pune", "summary": "", "entity": "ISM",
                       "status": "rejected", "gate": False, "source": "S"}])
    noop = _wait(admin_client, admin_client.post("/profiles/JCB/reevaluate").json())
    assert noop["status"] == "done" and noop["result"].get("newly_accepted", 0) == 0
    data = _profile(admin_client)["data"]
    data["strong_keywords"] = [*data["strong_keywords"], "Bamfordco"]
    admin_client.put("/profiles/JCB", json={"data": data})
    done = _wait(admin_client, admin_client.post("/profiles/JCB/reevaluate").json())
    assert done["status"] == "done", done
    assert done["result"].get("newly_accepted", 0) + done["result"].get("relevant_but_below_quality_gate", 0) == 1


def test_suggestions_require_confirmation(admin_client, db_engine) -> None:
    rows = [{"title": f"JCB India telematics rollout update {i}", "summary": "JCB telematics programme", "entity": "JCB", "source": f"S{i}"}
            for i in range(4)]
    _seed(db_engine, rows)
    made = admin_client.post("/profiles/JCB/suggestions/generate").json()
    assert made and all(s["status"] == "pending" and s["reason"] and s["example_article_ids"] for s in made)
    assert _profile(admin_client)["version"] == 0  # nothing takes effect yet
    pick = next(s for s in made if "telematics" in s["value"])
    assert admin_client.post(f"/profiles/JCB/suggestions/{pick['id']}", json={"accept": True}).json()["profile_version"] == 1
    assert pick["value"] in _profile(admin_client)["data"]["context_keywords"]
    assert admin_client.post(f"/profiles/JCB/suggestions/{pick['id']}", json={"accept": True}).status_code == 409


def test_profile_competitors_and_technologies_become_entities_and_feed_the_competitor_view(admin_client, db_engine) -> None:
    import asyncio
    from sqlalchemy import text as sql_text
    _seed(db_engine, [
        {"title": "Caterpillar launches new loader as JCB competes", "summary": "", "entity": "JCB", "source": "S1"},
        {"title": "Caterpillar expands plant", "summary": "", "entity": "ACE", "source": "S2"},  # attributed to ANOTHER client
    ])
    assert admin_client.get("/intelligence/competitors").json()["configured"] is False
    data = _profile(admin_client)["data"]
    data.update({"competitors": ["Caterpillar", "Komatsu"], "technologies": ["telematics"]})
    admin_client.put("/profiles/JCB", json={"data": data})
    view = admin_client.get("/intelligence/competitors").json()
    assert view["configured"] is True
    cat = next(c for c in view["competitors"] if c["competitor"] == "Caterpillar")
    assert cat["competitor_of"] == "JCB" and cat["article_count"] == 1 and cat["relationship_status"] == "unverified"  # ACE's article is not shown
    assert cat["basis"][0].startswith("profile:jcb@v1")
    komatsu = next(c for c in view["competitors"] if c["competitor"] == "Komatsu")
    assert komatsu["article_count"] == 0 and komatsu["coverage"] == "no stored article names this competitor"

    async def rels():
        async with db_engine.connect() as c:
            return (await c.execute(sql_text("select s.canonical_name, r.relationship_type, t.canonical_name from relationships r "
                                             "join entities s on s.id = r.source_entity_id join entities t on t.id = r.target_entity_id order by 1, 2"))).all()
    assert asyncio.run(rels()) == [("Caterpillar", "COMPETITOR_OF", "JCB"), ("JCB", "USES_TECHNOLOGY", "telematics"), ("Komatsu", "COMPETITOR_OF", "JCB")]
    data["competitors"] = ["Caterpillar"]
    admin_client.put("/profiles/JCB", json={"data": data})
    assert [r[0] for r in asyncio.run(rels())] == ["Caterpillar", "JCB"]  # Komatsu removed with the profile entry
