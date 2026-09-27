from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Article

def _source_payload(url: str, fixture_path: str) -> dict[str, str]:
    return {
        "name": "Synthetic M5 source",
        "url": url,
        "source_type": "RSS",
        "tier": "TIER_3",
        "fixture_path": fixture_path,
    }


def test_same_source_same_url_is_resolved_as_duplicate(admin_client) -> None:
    source = admin_client.post(
        "/sources",
        json=_source_payload("https://fixture.test/m5/repeat", "m4-example-feed.xml"),
    ).json()

    first = admin_client.post(f"/sources/{source['id']}/fetch")
    second = admin_client.post(f"/sources/{source['id']}/fetch")

    assert first.json()["articles_created"] == 2
    assert second.json()["articles_created"] == 0
    assert second.json()["articles_deduplicated"] == 2


def test_same_source_different_url_same_content_is_duplicate(admin_client) -> None:
    source = admin_client.post(
        "/sources",
        json=_source_payload("https://fixture.test/m5/content", "m4-example-feed.xml"),
    ).json()
    first = admin_client.post(f"/sources/{source['id']}/fetch")
    assert first.json()["articles_created"] == 2

    updated = admin_client.patch(
        f"/sources/{source['id']}",
        json={"fixture_path": "m5-same-content-different-url.xml"},
    )
    assert updated.status_code == 200
    second = admin_client.post(f"/sources/{source['id']}/fetch")

    assert second.status_code == 200
    assert second.json()["articles_created"] == 0
    assert second.json()["articles_deduplicated"] == 1


def test_same_source_different_content_remains_separate(admin_client) -> None:
    source = admin_client.post(
        "/sources",
        json=_source_payload("https://fixture.test/m5/different", "m4-example-feed.xml"),
    ).json()
    first = admin_client.post(f"/sources/{source['id']}/fetch")
    assert first.json()["articles_created"] == 2

    updated = admin_client.patch(
        f"/sources/{source['id']}",
        json={"fixture_path": "m5-different-content.xml"},
    )
    assert updated.status_code == 200
    second = admin_client.post(f"/sources/{source['id']}/fetch")

    assert second.status_code == 200
    assert second.json()["articles_created"] == 1
    assert second.json()["articles_deduplicated"] == 0


def test_different_sources_preserve_provenance(admin_client, db_engine) -> None:
    first_source = admin_client.post(
        "/sources",
        json=_source_payload("https://fixture.test/m5/source-one", "m4-example-feed.xml"),
    ).json()
    second_source = admin_client.post(
        "/sources",
        json=_source_payload(
            "https://fixture.test/m5/source-two", "m5-same-content-different-url.xml"
        ),
    ).json()

    first = admin_client.post(f"/sources/{first_source['id']}/fetch")
    second = admin_client.post(f"/sources/{second_source['id']}/fetch")

    assert first.json()["articles_created"] == 2
    assert second.json()["articles_created"] == 1
    assert second.json()["articles_deduplicated"] == 0

    async def read_source_ids():
        maker = async_sessionmaker(db_engine, expire_on_commit=False)
        async with maker() as session:
            rows = list((await session.scalars(select(Article))).all())
            return {str(row.source_id) for row in rows}

    assert asyncio.run(read_source_ids()) == {first_source["id"], second_source["id"]}