"""Evidence-gated, self-service company onboarding over the existing providers."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.clients import CLIENT_CONFIG
from app.adapters.ingestion.base import AdapterError
from app.adapters.ingestion.rss import RssHttpAdapter
from app.adapters.ingestion.site_adapters import ArticlePageAdapter
from app.models import DynamicClient, Entity, EntityAlias
from app.services.llm import LLMUnavailable, discover_company
from app.services.profiles import ProfileData, register_dynamic_profile, save_profile


class OnboardingError(ValueError):
    pass


def _domain(value: str) -> str:
    raw = value.strip()
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    host = (urlsplit(raw).hostname or "").lower().removeprefix("www.")
    if not host or "." not in host:
        raise OnboardingError("A valid company website/domain is required")
    return host


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")


async def suggest(query: str, limit: int = 8) -> list[dict]:
    value = query.strip()
    if "." not in value:
        return []
    url = value if value.startswith(("http://", "https://")) else f"https://{value}"
    documents = await _fetch_documents(url)
    return [{"name": document.title or url, "domain": urlsplit(document.canonical_url).hostname or "",
             "url": url, "snippet": document.normalized_content[:240], "provider": "website/rss"}
            for document in documents[:limit]]


async def _fetch_documents(website: str) -> list:
    source_id = uuid.uuid4()
    retrieved_at = datetime.now(timezone.utc)
    page_adapter = ArticlePageAdapter()
    documents = await page_adapter.fetch([website], source_id=source_id, retrieved_at=retrieved_at)
    base = website.rstrip("/")
    candidates = [website, f"{base}/rss", f"{base}/feed", f"{base}/news/rss", f"{base}/blog/feed"]
    rss = RssHttpAdapter()
    for feed_url in candidates[1:]:
        try:
            documents.extend(await rss.fetch(feed_url, source_id=source_id, retrieved_at=retrieved_at))
        except AdapterError:
            continue
    unique: dict[str, object] = {}
    for document in documents:
        if document.normalized_content.strip():
            unique.setdefault(document.canonical_url, document)
    if not unique:
        detail = "; ".join(page_adapter.errors[:2]) or "website returned no readable evidence"
        raise OnboardingError(f"Could not fetch readable company evidence: {detail}")
    return list(unique.values())


async def _discover(session: AsyncSession, query: str, domain: str, results: list[dict]) -> dict:
    evidence = [{"title": r["title"], "url": r["url"], "domain": r["domain"], "snippet": r["snippet"]} for r in results]
    user = f"QUERY: {query}\nREQUESTED DOMAIN: {domain}\nWEB RESULTS:\n{evidence}"
    try:
        data = await discover_company(session, user)
    except (LLMUnavailable, ValueError) as exc:
        raise OnboardingError("Company discovery could not produce a valid structured result") from exc
    return data.model_dump()


async def onboard(session: AsyncSession, *, query: str, website: str | None, actor: str) -> dict:
    query = " ".join(query.split())
    if not query:
        raise OnboardingError("Company name or website is required")
    if not website:
        raise OnboardingError("A company website is required for evidence-backed onboarding")
    requested_domain = _domain(website)
    documents = await _fetch_documents(website)
    results = [{"title": document.title or "", "url": document.canonical_url,
                "domain": (urlsplit(document.canonical_url).hostname or "").lower().removeprefix("www."),
                "snippet": document.normalized_content[:4000]} for document in documents]
    discovered = await _discover(session, query, requested_domain, results)
    name = " ".join(str(discovered.get("canonical_name", "")).split())
    official_domain = _domain(str(discovered.get("official_domain", "")))
    aliases = [str(x).strip() for x in discovered.get("aliases", []) if str(x).strip()]
    description = " ".join(str(discovered.get("description", "")).split())[:2000]
    if not name or not description:
        raise OnboardingError("Discovery did not return a company name and description")
    allowed = {r["url"] for r in results}
    cited = [x for x in discovered.get("sources", []) if isinstance(x, str) and x in allowed]
    domain_supported = any(r["domain"] == official_domain or r["domain"].endswith(f".{official_domain}") for r in results)
    requested_supported = official_domain == requested_domain or official_domain.endswith(f".{requested_domain}")
    name_supported = any(name.casefold() in f'{r["title"]} {r["snippet"]}'.casefold() for r in results)
    if not cited or not domain_supported or not requested_supported or not name_supported:
        raise OnboardingError("Company data could not be verified against independent web evidence")
    key = _key(name)
    if key in {_key(k) for k in CLIENT_CONFIG}:
        raise OnboardingError("This company is already onboarded")
    existing = await session.scalar(select(DynamicClient).where(DynamicClient.client_key == key))
    if existing:
        raise OnboardingError("This company is already onboarded")
    profile = ProfileData(
        display_name=name, description=description, legal_names=[name], aliases=aliases,
        official_domains=[official_domain], industries=[str(x).strip() for x in discovered.get("industries", []) if str(x).strip()],
        strong_keywords=[name, *aliases], context_keywords=[str(x).strip() for x in discovered.get("keywords", []) if str(x).strip()],
        trusted_sources=sorted({urlsplit(url).hostname.removeprefix("www.") for url in cited if urlsplit(url).hostname}),
        google_queries=[f'"{name}"', f'"{name}" news'],
    )
    register_dynamic_profile(profile)
    entity = Entity(entity_type="ORG", canonical_name=name, canonical_key=key, description=description, status="verified")
    session.add(entity)
    await session.flush()
    for alias in sorted({name, *aliases}, key=str.casefold):
        session.add(EntityAlias(entity_id=entity.id, alias=alias))
    session.add(DynamicClient(client_key=key, canonical_name=name, official_domain=official_domain, description=description,
                              evidence=[r for r in results if r["url"] in cited], created_by=actor))
    await save_profile(session, name, profile, actor=actor, note="Self-service company onboarding")
    return {"client_key": key, "canonical_name": name, "official_domain": official_domain, "description": description,
            "evidence": [r for r in results if r["url"] in cited], "status": "verified"}


async def load_dynamic_clients(session: AsyncSession) -> None:
    try:
        rows = (await session.scalars(select(DynamicClient).order_by(DynamicClient.canonical_name))).all()
    except ProgrammingError:
        # Allow an old deployment to boot while its normal migration step is pending.
        await session.rollback()
        return
    for row in rows:
        from app.models.architecture import ClientProfile
        profile_row = await session.scalar(select(ClientProfile).where(ClientProfile.client_key == row.client_key, ClientProfile.is_active.is_(True)))
        if profile_row:
            register_dynamic_profile(ProfileData.model_validate(profile_row.data))