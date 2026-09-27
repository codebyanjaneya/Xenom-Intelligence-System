from __future__ import annotations

import uuid


def _source_payload(name: str = "Persisted RSS source") -> dict[str, str]:
    return {
        "name": name,
        "url": f"https://fixture.test/{name.lower().replace(' ', '-')}",
        "source_type": "RSS",
        "tier": "TIER_3",
        "fixture_path": "m4-example-feed.xml",
    }


def test_authenticated_user_can_list_persisted_articles(admin_client) -> None:
    source = admin_client.post("/sources", json=_source_payload())
    assert source.status_code == 201, source.text
    source_id = source.json()["id"]

    fetched = admin_client.post(f"/sources/{source_id}/fetch")
    assert fetched.status_code == 200, fetched.text

    response = admin_client.get("/articles")

    assert response.status_code == 200, response.text
    articles = response.json()
    assert len(articles) == 2
    assert articles[0]["id"]
    assert articles[0]["title"]
    assert articles[0]["url"].startswith("https://fixture.test/")
    assert articles[0]["source_name"] == "Persisted RSS source"
    assert articles[0]["summary"]
    assert articles[0]["fetched_at"]


def test_articles_support_source_filter_and_limit(admin_client) -> None:
    source = admin_client.post("/sources", json=_source_payload("Filtered source"))
    source_id = source.json()["id"]
    assert admin_client.post(f"/sources/{source_id}/fetch").status_code == 200

    by_id = admin_client.get(f"/articles?source_id={source_id}&limit=1")
    by_name = admin_client.get("/articles?source_name=Filtered%20source")

    assert by_id.status_code == 200
    assert len(by_id.json()) == 1
    assert by_id.json()[0]["source_id"] == source_id
    assert by_name.status_code == 200
    assert len(by_name.json()) == 2


def test_viewer_can_list_articles(viewer_client) -> None:
    response = viewer_client.get("/articles")

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_anonymous_cannot_list_articles(anon_client) -> None:
    response = anon_client.get("/articles")

    assert response.status_code == 401


def test_invalid_article_limit_is_rejected(admin_client) -> None:
    response = admin_client.get("/articles?limit=0")

    assert response.status_code == 422
