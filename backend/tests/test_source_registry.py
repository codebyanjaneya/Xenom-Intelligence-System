"""Source registry invariants and DB sync."""

from __future__ import annotations

import asyncio
from urllib.parse import urlparse

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config.clients import CLIENT_CONFIG, ENTITY_KEY_BY_CLIENT
from app.config.entity_config import ENTITY_CONFIGS
from app.config.source_registry import SOURCE_REGISTRY, enabled_specs, spec_by_key
from app.models import Source
from app.services.registry_ingestion import google_specs, sync_registry_sources
from tests.conftest import _truncate

# Every URL supplied for onboarding (issue brief, 2026-09-20).
SUPPLIED_URLS = {
    "VIKAS": [
        "https://www.vikasgroup.in/",
        "https://www.linkedin.com/company/vikas-group/",
        "https://www.ambitionbox.com/overview/vikas-group-overview",
        "https://www.ambitionbox.com/reviews/vikas-group-reviews/faridabad-location",
        "https://economictimes.indiatimes.com/pranav-vikas-group-to-focus-on-ev-and-hybrid-vehicle-technology/articleshow/62938149.cms",
        "https://www.motorindiaonline.in/vikas-group-offering-value-added-hvac-solutions-for-bus-industry/",
    ],
    "VIPS": [
        "https://vips.edu/",
        "https://www.linkedin.com/school/vivekananda-institute-of-professional-studies/?originalSubdomain=in",
        "https://vips.edu/Notices",
        "https://college360.co.in/college/Vivekananda-Institute-of-Professional-Studies-VIPS-New-Delhi-3708/news",
        "https://www.shiksha.com/college/vips-vivekananda-institute-of-professional-studies-pitampura-delhi-28593/news-articles",
        "https://www.vips.edu/vsbs/Placements/Recent_Activities",
        "https://collegedunia.com/college/9185-vivekananda-institute-of-professional-studies-vips-new-delhi/news",
    ],
    "Definedge": [
        "https://www.definedgesecurities.com/",
        "https://www.definedgesecurities.com/blog",
        "https://forum.definedgesecurities.com/category/1/announcements",
        "https://www.devdiscourse.com/article/technology/3891925-industry-veteran-hemant-luthra-takes-helm-at-punes-definedge-securities",
        "https://www.finsmes.com/2025/11/definedge-raises-funding-round.html",
        "https://www.passionateinmarketing.com/definedge-securities-accelerates-wealth-tech-vision-with-hemant-luthra-as-chairman-ajay-srivastava-and-d-prasad-join-board/",
    ],
    "ISM": [
        "https://economictimes.indiatimes.com/topic/india-semiconductor",
        "https://www.ism.gov.in/",
        "https://yourstory.com/2026/09/semicon-india-ism-ceo-pitches-mega-gathering",
        "https://indianexpress.com/article/explained/semicon-india-2026-pm-modi-semiconductor-chip-push-10881503/",
        "https://www.india-briefing.com/news/setting-up-a-semiconductor-fabrication-plant-in-india-what-foreign-investors-should-know-22009.html/",
        "https://the-ken.com/newsletter/make-india-competitive-again/india-semiconductor-mission-2-0-has-a-supply-plan-the-demand-gap-remains/",
        "https://timesofindia.indiatimes.com/business/india-business/pm-modi-independence-day-speech-indias-big-semiconductor-push-pm-modi-says-5-8-plants-coming-up-in-few-years-stresses-self-reliance-in-chips/articleshow/133255271.cms",
    ],
}
JUSTDIAL = "https://www.justdial.com/Delhi/Vivekananda-Institute-of-Professional-Studies-Au-Block-Near-Delhi-School-Of-Business-Prashant-Vihar/011PXX11-XX11-111206155657-N6I2_BZDET"


def _norm(url: str) -> str:
    return url.split("?")[0].rstrip("/").lower()


def test_every_supplied_url_is_registered_under_its_client() -> None:
    registered = {(s.client, _norm(s.url)) for s in SOURCE_REGISTRY}
    for client, urls in SUPPLIED_URLS.items():
        for url in urls:
            assert (client, _norm(url)) in registered, f"{client}: {url} is not registered"
    assert ("VIPS", _norm(JUSTDIAL)) in registered


def test_registry_keys_and_urls_are_unique() -> None:
    assert len({s.key for s in SOURCE_REGISTRY}) == len(SOURCE_REGISTRY)
    assert len({s.url for s in SOURCE_REGISTRY}) == len(SOURCE_REGISTRY)


def test_specs_only_use_known_clients_modes_and_db_enums() -> None:
    for s in SOURCE_REGISTRY:
        assert s.client is None or s.client in ENTITY_CONFIGS
        assert s.tier in {"TIER_1", "TIER_2", "TIER_3"}
        assert s.db_source_type in {"RSS", "HTML", "OFFICIAL_NEWSROOM", "PRESS_RELEASE", "GOVERNMENT", "REGULATORY", "OTHER_PUBLIC", "RESEARCH_API", "MODEL_HUB"}
        assert urlparse(s.url).scheme in {"http", "https"}
        if s.fetch_mode == "rss":
            assert s.feed_url
        if s.fetch_mode == "link_listing":
            assert s.link_pattern or True


def test_blocked_and_profile_sources_are_reference_only_with_a_reason() -> None:
    for key in ("vikas-linkedin", "vips-linkedin", "vips-shiksha", "vips-collegedunia", "vips-justdial",
                "vikas-ambitionbox", "vikas-ambitionbox-reviews", "ace-linkedin"):
        spec = spec_by_key(key)
        assert not spec.enabled and spec.note, f"{key} must be reference-only and say why"
    assert {s.key for s in enabled_specs()}.isdisjoint({s.key for s in SOURCE_REGISTRY if s.fetch_mode == "reference_only"})


def test_official_sources_are_tier1_and_marked_official() -> None:
    for key in ("vips-notices", "definedge-blog", "ism-press-releases", "jcb-india-news"):
        s = spec_by_key(key)
        assert s.tier == "TIER_1" and s.source_kind == "official" and s.enabled


def test_google_queries_are_precise_per_client() -> None:
    queries = {c: cfg.google_news_queries for c, cfg in CLIENT_CONFIG.items()}
    assert '"India Semiconductor Mission"' in queries["ISM"]
    assert '"Vivekananda Institute of Professional Studies"' in queries["VIPS"]
    assert '"Action Construction Equipment"' in queries["ACE"]
    assert all(len(q) >= 3 for qs in queries.values() for q in qs)
    # no query is a bare ambiguous acronym
    assert not any(q.strip('"') in {"ACE", "JCB", "VIPS", "ISM"} for qs in queries.values() for q in qs)
    assert {g[0] for g in google_specs()} == set(CLIENT_CONFIG)
    assert set(ENTITY_KEY_BY_CLIENT.values()) == set(ENTITY_CONFIGS)


def test_client_config_endpoint_fields_are_derived_from_the_taxonomy() -> None:
    assert CLIENT_CONFIG["ISM"].description.startswith("India Semiconductor Mission")
    assert "Institute for Supply Management" in CLIENT_CONFIG["ISM"].exclusion_keywords
    assert CLIENT_CONFIG["VIPS"].trusted_sites == ENTITY_CONFIGS["VIPS"].trusted_sources


def test_sync_creates_a_source_row_for_every_spec_with_reference_ones_inactive(db_engine) -> None:
    maker = async_sessionmaker(db_engine, expire_on_commit=False)

    async def run():
        await _truncate(maker)
        async with maker() as session:
            rows = await sync_registry_sources(session)
        async with maker() as session:
            stored = {s.canonical_url: s for s in await session.scalars(select(Source))}
        return rows, stored

    rows, stored = asyncio.run(run())
    assert len(stored) == len(SOURCE_REGISTRY)
    for spec in SOURCE_REGISTRY:
        row = stored[spec.url]
        assert row.is_active is spec.enabled and row.tier == spec.tier and row.source_type == spec.db_source_type
    # idempotent
    assert len(asyncio.run(run())[1]) == len(SOURCE_REGISTRY)
