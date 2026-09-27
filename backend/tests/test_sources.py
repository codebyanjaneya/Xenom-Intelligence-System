"""Source registry integration tests (M3)."""

from __future__ import annotations

import uuid


def _payload(url="https://news.example.com/feed", **overrides):
    body = {
        "name": "Example Source",
        "url": url,
        "source_type": "RSS",
        "tier": "TIER_1",
    }
    body.update(overrides)
    return body


def _create(client, **overrides):
    resp = client.post("/sources", json=_payload(**overrides))
    assert resp.status_code == 201, resp.text
    return resp.json()


# 1
def test_admin_can_create_source(admin_client):
    body = _create(admin_client)
    assert body["name"] == "Example Source"
    assert body["source_type"] == "RSS"
    assert body["tier"] == "TIER_1"
    assert body["is_active"] is True
    assert body["canonical_url"] == "https://news.example.com/feed"


# 2
def test_viewer_cannot_create_source(viewer_client):
    resp = viewer_client.post("/sources", json=_payload())
    assert resp.status_code == 403


# 3
def test_unauthenticated_create(anon_client):
    resp = anon_client.post("/sources", json=_payload())
    assert resp.status_code == 401


# 4
def test_admin_can_list(admin_client):
    _create(admin_client)
    resp = admin_client.get("/sources")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


# 5
def test_viewer_can_list(viewer_client):
    resp = viewer_client.get("/sources")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# 6
def test_unauthenticated_list(anon_client):
    resp = anon_client.get("/sources")
    assert resp.status_code == 401


# 7
def test_admin_can_update(admin_client):
    src = _create(admin_client)
    resp = admin_client.patch(f"/sources/{src['id']}", json={"name": "Renamed", "tier": "TIER_2"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed"
    assert resp.json()["tier"] == "TIER_2"


# 8
def test_viewer_cannot_update(viewer_client):
    resp = viewer_client.patch(f"/sources/{uuid.uuid4()}", json={"name": "x"})
    assert resp.status_code == 403


# 9
def test_admin_can_deactivate(admin_client):
    src = _create(admin_client)
    resp = admin_client.delete(f"/sources/{src['id']}")
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


# 10
def test_viewer_cannot_deactivate(viewer_client):
    resp = viewer_client.delete(f"/sources/{uuid.uuid4()}")
    assert resp.status_code == 403


# 11
def test_invalid_url_rejected(admin_client):
    resp = admin_client.post("/sources", json=_payload(url="not-a-valid-url"))
    assert resp.status_code == 422


# 12
def test_invalid_source_type_rejected(admin_client):
    resp = admin_client.post("/sources", json=_payload(source_type="BLOG"))
    assert resp.status_code == 422


# 13
def test_invalid_tier_rejected(admin_client):
    resp = admin_client.post("/sources", json=_payload(tier="TIER_9"))
    assert resp.status_code == 422


# 14
def test_duplicate_canonical_url_rejected(admin_client):
    _create(admin_client, url="https://dup.example.com/a")
    # Same canonical URL (trailing slash normalized away) -> conflict.
    resp = admin_client.post("/sources", json=_payload(url="https://dup.example.com/a/"))
    assert resp.status_code == 409


# 15
def test_get_source_by_id(admin_client):
    src = _create(admin_client)
    resp = admin_client.get(f"/sources/{src['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == src["id"]


# 16
def test_missing_source_404(admin_client):
    resp = admin_client.get(f"/sources/{uuid.uuid4()}")
    assert resp.status_code == 404


# 17
def test_source_health_retrieval(admin_client):
    src = _create(admin_client)
    resp = admin_client.get(f"/sources/{src['id']}/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source_id"] == src["id"]
    # No fetching happens in M3, so there is no health record yet.
    assert body["latest"] is None


# 18
def test_no_sensitive_fields_in_source_api(admin_client):
    _create(admin_client)
    listing = admin_client.get("/sources")
    assert "password_hash" not in listing.text
    assert "access_token" not in listing.text
