"""Fine-tuning architecture (manager-requested extension): dataset export + job records, never a fabricated result."""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from tests.test_article_search import _seed


def _label_rows(db_engine, article_ids):
    async def run():
        async with db_engine.begin() as conn:
            for aid, label in article_ids:
                await conn.execute(text(
                    "insert into article_labels (id, article_id, label, method, confidence, taxonomy_version) "
                    "values (gen_random_uuid(), :aid, :label, 'rule', 0.8, '1')"
                ), {"aid": aid, "label": label})
    asyncio.run(run())


def test_dataset_requires_real_reviewed_data_and_is_reproducible(admin_client, db_engine) -> None:
    ids = _seed(db_engine, [
        {"title": "JCB launches new excavator range", "summary": "Launch details.", "entity": "JCB", "source": "S1"},
        {"title": "JCB reports quarterly revenue", "summary": "Revenue rose.", "entity": "JCB", "source": "S2"},
    ])
    empty = admin_client.post("/engine/fine-tuning/datasets", json={"name": "empty-set", "kind": "taxonomy_labels"})
    assert empty.status_code == 422 and "no reviewed stored data" in empty.text

    _label_rows(db_engine, [(ids["JCB launches new excavator range"], "company"), (ids["JCB reports quarterly revenue"], "company")])
    created = admin_client.post("/engine/fine-tuning/datasets", json={"name": "taxonomy-company-v1", "kind": "taxonomy_labels", "label": "company"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["item_count"] == 2 and body["kind"] == "taxonomy_labels" and body["digest"]

    listed = admin_client.get("/engine/fine-tuning/datasets").json()
    assert any(d["name"] == "taxonomy-company-v1" for d in listed)

    dup = admin_client.post("/engine/fine-tuning/datasets", json={"name": "taxonomy-company-v1", "kind": "taxonomy_labels"})
    assert dup.status_code == 422 and "exists" in dup.text


def test_job_is_created_blocked_without_a_configured_provider_never_fabricated(admin_client, db_engine) -> None:
    ids = _seed(db_engine, [{"title": "JCB launches new excavator range", "summary": "Launch.", "entity": "JCB", "source": "S1"}])
    _label_rows(db_engine, [(ids["JCB launches new excavator range"], "company")])
    dataset_id = admin_client.post("/engine/fine-tuning/datasets", json={"name": "taxonomy-set-2", "kind": "taxonomy_labels"}).json()["id"]

    job = admin_client.post("/engine/fine-tuning/jobs", json={"dataset_id": dataset_id, "base_model": "some-base-model", "config": {"epochs": 1}})
    assert job.status_code == 201, job.text
    body = job.json()
    assert body["status"] == "blocked" and "no fine-tuning provider" in body["status_reason"].lower()

    listed = admin_client.get("/engine/fine-tuning/jobs").json()
    assert any(j["id"] == body["id"] and j["status"] == "blocked" for j in listed)


def test_viewer_cannot_create_datasets_or_jobs(viewer_client) -> None:
    assert viewer_client.post("/engine/fine-tuning/datasets", json={"name": "x", "kind": "taxonomy_labels"}).status_code == 403
    import uuid
    assert viewer_client.post("/engine/fine-tuning/jobs", json={"dataset_id": str(uuid.uuid4()), "base_model": "m"}).status_code == 403
