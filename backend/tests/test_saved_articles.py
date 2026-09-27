from __future__ import annotations


def _source_payload() -> dict[str, str]:
    return {
        "name": "Saved RSS source",
        "url": "https://fixture.test/saved-rss-source",
        "source_type": "RSS",
        "tier": "TIER_3",
        "fixture_path": "m4-example-feed.xml",
    }


def test_user_can_save_list_and_remove_article(admin_client) -> None:
    source = admin_client.post("/sources", json=_source_payload())
    assert source.status_code == 201, source.text
    source_id = source.json()["id"]
    assert admin_client.post(f"/sources/{source_id}/fetch").status_code == 200
    article = admin_client.get("/articles?limit=1").json()[0]

    saved = admin_client.put(f"/articles/{article['id']}/saved")
    assert saved.status_code == 200, saved.text
    assert saved.json()["id"] == article["id"]
    assert [item["id"] for item in admin_client.get("/articles/saved").json()] == [article["id"]]

    removed = admin_client.delete(f"/articles/{article['id']}/saved")
    assert removed.status_code == 204
    assert admin_client.get("/articles/saved").json() == []
