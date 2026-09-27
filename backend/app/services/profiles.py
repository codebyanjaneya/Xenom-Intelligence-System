"""Versioned client profiles and the loader that turns them into the rule engine's configuration (PDF s.7, s.12).

The code configuration (``entity_config.py`` / ``clients.py``) stays the baseline and the fallback: a client without a
saved profile behaves exactly as before. A saved profile is an immutable numbered version; saving creates a new version
and activates it, rollback re-creates an older version as a new one, and every change is written to ``config_audit``.

The loader overlays the ACTIVE version onto the live ``ENTITY_CONFIGS`` / ``CLIENT_CONFIG`` registries in place, so the
existing relevance engine, entity matcher and query builder read edited data without any second code path.
"""
from __future__ import annotations

import copy
import dataclasses
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.clients import CLIENT_CONFIG, ENTITY_KEY_BY_CLIENT, ClientConfig, register_dynamic_client
from app.config.entity_config import ENTITY_CONFIGS, EntityConfig
from app.models.architecture import ClientProfile, ConfigAudit

logger = logging.getLogger(__name__)

MAX_ITEMS = 300
MAX_ITEM_LENGTH = 200

_LIST_FIELDS = (
    "legal_names", "aliases", "subsidiaries", "products", "official_domains", "industries", "sub_industries",
    "strong_keywords", "ambiguous_names", "ambiguous_context", "case_sensitive_acronyms", "context_keywords",
    "executives", "locations", "negative_keywords", "hard_negative_keywords", "trusted_sources",
    "competitors", "technologies", "ai_model_interests", "research_interests", "people",
    "google_queries", "search_queries",
)

# Baseline snapshot of the code configuration, taken once at import; refresh_configs resets to it before overlaying.
_BASE_ENTITY: dict[str, EntityConfig] = {k: copy.deepcopy(v) for k, v in ENTITY_CONFIGS.items()}
_BASE_CLIENT: dict[str, ClientConfig] = dict(CLIENT_CONFIG)


def register_dynamic_profile(data: "ProfileData") -> str:
    """Add a runtime client profile to the existing matcher/query registries."""
    name = data.display_name.strip()
    key = name
    entity = EntityConfig(
        canonical_name=name, legal_names=list(data.legal_names), aliases=list(data.aliases),
        subsidiaries=list(data.subsidiaries), products=list(data.products), official_domains=list(data.official_domains),
        industry_terms=list(data.industries), excluded_entities=[], excluded_keywords=list(data.hard_negative_keywords),
        required_context_terms=list(data.context_keywords) or None, confidence_threshold=35,
        display_name=name, strong_keywords=list(data.strong_keywords), ambiguous_names=list(data.ambiguous_names),
        ambiguous_context=list(data.ambiguous_context), case_sensitive_acronyms=list(data.case_sensitive_acronyms),
        context_keywords=list(data.context_keywords), executive_keywords=list(data.executives),
        location_keywords=list(data.locations), topic_keywords={k: list(v) for k, v in data.topic_keywords.items()},
        negative_keywords=list(data.negative_keywords), hard_negative_keywords=list(data.hard_negative_keywords),
        trusted_sources=list(data.trusted_sources), source_priority=dict(data.source_priority) or {"official": 15, "trusted": 8, "other": 0},
    )
    cfg = ClientConfig(
        canonical_name=name, aliases=list(data.aliases), keywords=list(data.context_keywords),
        technology_keywords=list(data.technologies), industry_keywords=list(data.industries),
        google_news_queries=list(data.google_queries), trusted_sites=list(data.trusted_sources),
        exclusion_keywords=list(data.hard_negative_keywords) + list(data.negative_keywords), description=data.description,
    )
    ENTITY_CONFIGS[key] = entity
    _BASE_ENTITY[key] = copy.deepcopy(entity)
    _BASE_CLIENT[name] = cfg
    register_dynamic_client(cfg, key)
    return name


class ProfileData(BaseModel):
    """The editable client profile (PDF s.7 tree). Rule compounds stay in code; everything below is data."""

    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(default="", max_length=MAX_ITEM_LENGTH)
    description: str = Field(default="", max_length=2000)
    legal_names: list[str] = []
    aliases: list[str] = []
    subsidiaries: list[str] = []
    products: list[str] = []
    official_domains: list[str] = []
    industries: list[str] = []
    sub_industries: list[str] = []
    strong_keywords: list[str] = []
    ambiguous_names: list[str] = []
    ambiguous_context: list[str] = []
    case_sensitive_acronyms: list[str] = []
    context_keywords: list[str] = []
    executives: list[str] = []
    locations: list[str] = []
    negative_keywords: list[str] = []
    hard_negative_keywords: list[str] = []
    trusted_sources: list[str] = []
    source_priority: dict[str, int] = {}
    topic_keywords: dict[str, list[str]] = {}
    competitors: list[str] = []
    competitor_notes: dict[str, str] = {}
    """Per-competitor provenance: 'competitor name' -> 'reason (business/product/market overlap); source: URL (checked <date>)'.
    Written when a competitor mapping is established from external research rather than typed freehand. Optional."""
    technologies: list[str] = []
    ai_model_interests: list[str] = []
    research_interests: list[str] = []
    people: list[str] = []
    google_queries: list[str] = []
    search_queries: list[str] = []
    source_weights: dict[str, float] = {}

    @field_validator(*_LIST_FIELDS)
    @classmethod
    def _clean_list(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = " ".join(str(item).split())
            if not text:
                continue
            if len(text) > MAX_ITEM_LENGTH:
                raise ValueError(f"item longer than {MAX_ITEM_LENGTH} characters")
            if "\x00" in text:
                raise ValueError("NUL characters are not allowed")
            key = text.casefold()
            if key not in seen:
                seen.add(key)
                cleaned.append(text)
        if len(cleaned) > MAX_ITEMS:
            raise ValueError(f"more than {MAX_ITEMS} items")
        return cleaned

    @field_validator("source_priority")
    @classmethod
    def _priority_range(cls, value: dict[str, int]) -> dict[str, int]:
        for key, weight in value.items():
            if not 0 <= weight <= 30:
                raise ValueError(f"source priority for {key} must be 0-30")
        return value

    @field_validator("source_weights")
    @classmethod
    def _weights_range(cls, value: dict[str, float]) -> dict[str, float]:
        for key, weight in value.items():
            if not 0 <= weight <= 1:
                raise ValueError(f"source weight for {key} must be between 0 and 1")
        return value

    @field_validator("topic_keywords")
    @classmethod
    def _topics(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        return {" ".join(k.split()): [" ".join(t.split()) for t in v if t and t.strip()][:MAX_ITEMS] for k, v in value.items() if k.strip()}

    @field_validator("competitor_notes")
    @classmethod
    def _competitor_notes(cls, value: dict[str, str]) -> dict[str, str]:
        out = {}
        for key, note in value.items():
            key, note = " ".join(key.split()), " ".join(str(note).split())
            if not key or not note:
                continue
            if len(note) > 1000:
                raise ValueError(f"competitor note for {key} longer than 1000 characters")
            out[key] = note
        return out


class UnknownProfileClient(ValueError):
    pass


class ProfileVersionNotFound(ValueError):
    pass


def resolve(client: str) -> tuple[str, str]:
    """(client_key, canonical_name) for a configured client, case-insensitive."""
    for name in CLIENT_CONFIG:
        if name.lower() == client.strip().lower():
            return name.lower(), name
    raise UnknownProfileClient(f"unknown client: {client}")


def code_profile(client: str) -> ProfileData:
    """The profile as defined in code (baseline and fallback)."""
    _, name = resolve(client)
    entity = _BASE_ENTITY[ENTITY_KEY_BY_CLIENT[name]]
    cfg = _BASE_CLIENT[name]
    return ProfileData(
        display_name=entity.display_name or cfg.canonical_name, description=cfg.description or "",
        legal_names=entity.legal_names, aliases=sorted({*entity.aliases, *cfg.aliases}, key=str.casefold),
        subsidiaries=entity.subsidiaries, products=entity.products, official_domains=entity.official_domains,
        industries=entity.industry_terms, strong_keywords=entity.strong_keywords, ambiguous_names=entity.ambiguous_names,
        ambiguous_context=entity.ambiguous_context, case_sensitive_acronyms=entity.case_sensitive_acronyms,
        context_keywords=entity.context_keywords, executives=entity.executive_keywords, locations=entity.location_keywords,
        negative_keywords=entity.negative_keywords, hard_negative_keywords=entity.hard_negative_keywords,
        trusted_sources=entity.trusted_sources, source_priority=dict(entity.source_priority),
        topic_keywords={k: list(v) for k, v in entity.topic_keywords.items()},
        google_queries=list(cfg.google_news_queries),
    )


def apply_overlay(client: str, data: ProfileData | None) -> None:
    """Make the rule engine and query builder read ``data`` (None restores the code baseline)."""
    _, name = resolve(client)
    entity_key = ENTITY_KEY_BY_CLIENT[name]
    base_entity, base_client = _BASE_ENTITY[entity_key], _BASE_CLIENT[name]
    if data is None:
        ENTITY_CONFIGS[entity_key] = copy.deepcopy(base_entity)
        CLIENT_CONFIG[name] = base_client
        return
    ENTITY_CONFIGS[entity_key] = dataclasses.replace(
        base_entity, display_name=data.display_name or base_entity.display_name,
        legal_names=list(data.legal_names), aliases=list(data.aliases), subsidiaries=list(data.subsidiaries),
        products=list(data.products), official_domains=list(data.official_domains), industry_terms=list(data.industries),
        strong_keywords=list(data.strong_keywords), ambiguous_names=list(data.ambiguous_names),
        ambiguous_context=list(data.ambiguous_context), case_sensitive_acronyms=list(data.case_sensitive_acronyms),
        context_keywords=list(data.context_keywords), executive_keywords=list(data.executives),
        location_keywords=list(data.locations), negative_keywords=list(data.negative_keywords),
        hard_negative_keywords=list(data.hard_negative_keywords), trusted_sources=list(data.trusted_sources),
        source_priority=dict(data.source_priority) or dict(base_entity.source_priority),
        topic_keywords={k: list(v) for k, v in data.topic_keywords.items()} or copy.deepcopy(base_entity.topic_keywords),
    )
    CLIENT_CONFIG[name] = dataclasses.replace(
        base_client, aliases=list(data.aliases), description=data.description or base_client.description,
        google_news_queries=list(data.google_queries) or list(base_client.google_news_queries),
        keywords=list(data.context_keywords), industry_keywords=list(data.industries),
        trusted_sites=list(data.trusted_sources),
        exclusion_keywords=list(data.hard_negative_keywords) + list(data.negative_keywords),
    )


def entity_config_for(client: str, data: ProfileData | None) -> EntityConfig:
    """The EntityConfig a profile would produce, WITHOUT touching the live registries (used by impact preview)."""
    _, name = resolve(client)
    entity_key = ENTITY_KEY_BY_CLIENT[name]
    live, live_client = ENTITY_CONFIGS[entity_key], CLIENT_CONFIG[name]
    try:
        apply_overlay(name, data)
        return ENTITY_CONFIGS[entity_key]
    finally:
        ENTITY_CONFIGS[entity_key], CLIENT_CONFIG[name] = live, live_client


async def active_row(session: AsyncSession, client_key: str) -> ClientProfile | None:
    return await session.scalar(select(ClientProfile).where(ClientProfile.client_key == client_key, ClientProfile.is_active.is_(True)))


async def effective_profile(session: AsyncSession, client: str) -> tuple[int, ProfileData, str]:
    """(version, data, origin) - origin is 'saved' for a stored version, 'code' for the fallback (version 0)."""
    key, _ = resolve(client)
    row = await active_row(session, key)
    if row is None:
        return 0, code_profile(client), "code"
    return row.version, ProfileData.model_validate(row.data), "saved"


async def refresh_configs(session: AsyncSession) -> dict[str, int]:
    """Overlay every client's active saved profile on the live rule configuration; others revert to code. {client: version}."""
    rows = {r.client_key: r for r in (await session.scalars(select(ClientProfile).where(ClientProfile.is_active.is_(True)))).all()}
    applied: dict[str, int] = {}
    for name in list(CLIENT_CONFIG):
        row = rows.get(name.lower())
        if row is None:
            apply_overlay(name, None)
            continue
        try:
            apply_overlay(name, ProfileData.model_validate(row.data))
            applied[name] = row.version
        except Exception as exc:  # noqa: BLE001 - a corrupt profile must never stop ingestion; fall back to code
            logger.error("Profile %s v%s is invalid, using code configuration: %s", name, row.version, exc)
            apply_overlay(name, None)
    return applied


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for key in sorted({*before, *after}):
        b, a = before.get(key), after.get(key)
        if b == a:
            continue
        if isinstance(b, list) and isinstance(a, list):
            out[key] = {"added": [x for x in a if x not in b], "removed": [x for x in b if x not in a]}
        else:
            out[key] = {"from": b, "to": a}
    return out


async def audit(session: AsyncSession, *, actor: str | None, action: str, object_type: str, object_key: str,
                before: dict | None = None, after: dict | None = None) -> None:
    session.add(ConfigAudit(actor=actor, action=action, object_type=object_type, object_key=object_key, before=before, after=after))


async def _next_version(session: AsyncSession, client_key: str) -> int:
    rows = await session.scalars(select(ClientProfile.version).where(ClientProfile.client_key == client_key))
    return max(rows.all() or [0]) + 1


async def save_profile(session: AsyncSession, client: str, data: ProfileData, *, actor: str | None, note: str | None = None,
                       reverted_from: int | None = None) -> ClientProfile:
    key, name = resolve(client)
    _, before, _ = await effective_profile(session, name)
    version = await _next_version(session, key)
    await session.execute(update(ClientProfile).where(ClientProfile.client_key == key, ClientProfile.is_active.is_(True)).values(is_active=False))
    row = ClientProfile(client_key=key, version=version, data=data.model_dump(mode="json"), is_active=True,
                        created_by=actor, change_note=note, reverted_from=reverted_from)
    session.add(row)
    await sync_profile_entities(session, name, data, version)
    await audit(session, actor=actor, action="profile.rollback" if reverted_from else "profile.save", object_type="client_profile",
                object_key=f"{key}@v{version}", before=before.model_dump(mode="json"), after=row.data)
    await session.commit()
    await refresh_configs(session)
    return row


async def rollback(session: AsyncSession, client: str, version: int, *, actor: str | None) -> ClientProfile:
    key, _ = resolve(client)
    old = await session.scalar(select(ClientProfile).where(ClientProfile.client_key == key, ClientProfile.version == version))
    if old is None:
        raise ProfileVersionNotFound(f"{key} has no version {version}")
    return await save_profile(session, client, ProfileData.model_validate(old.data), actor=actor,
                              note=f"rollback to v{version}", reverted_from=version)


async def list_versions(session: AsyncSession, client: str) -> list[ClientProfile]:
    key, _ = resolve(client)
    return list((await session.scalars(select(ClientProfile).where(ClientProfile.client_key == key).order_by(ClientProfile.version.desc()))).all())


PROFILE_NOTE = "profile:"


async def sync_profile_entities(session: AsyncSession, client: str, data: ProfileData, version: int) -> dict:
    """PDF s.14 (phase 4): competitor and technology ENTITIES and their relationships come from the client profile.
    COMPETITOR_OF runs competitor -> client, USES_TECHNOLOGY client -> technology. They are marked unverified with a
    ``profile:<client>@v<n>`` evidence note (a manager's configuration, not a fact found in an article), and relationships this sync
    created for the client that are no longer in the profile are removed."""
    from app.models.entities import Entity, Relationship, RelationshipEvidence

    key, name = resolve(client)
    org = await session.scalar(select(Entity).where(Entity.canonical_key == key, Entity.entity_type == "ORG"))
    if org is None:
        return {"skipped": "client entity is not registered"}
    tag = f"{PROFILE_NOTE}{key}"
    wanted: dict[tuple[str, str], tuple[str, str, str]] = {}
    for competitor in data.competitors:
        wanted[("COMPETITOR_OF", competitor.casefold())] = ("ORG", competitor, "competitor")
    for technology in data.technologies:
        wanted[("USES_TECHNOLOGY", technology.casefold())] = ("TECHNOLOGY", technology, "technology")
    existing = (await session.execute(
        select(Relationship, RelationshipEvidence).join(RelationshipEvidence, RelationshipEvidence.relationship_id == Relationship.id)
        .where(RelationshipEvidence.note.like(f"{tag}@%")))).all()
    keep: set = set()
    created = 0
    for (rel_type, _cf), (entity_type, label, _kind) in wanted.items():
        target_key = label.strip().lower()
        entity = await session.scalar(select(Entity).where(Entity.canonical_key == target_key))
        if entity is None:
            entity = Entity(entity_type=entity_type, canonical_name=label, canonical_key=target_key, status="needs_review")
            session.add(entity)
            await session.flush()
        if entity.entity_type != entity_type or entity.id == org.id:
            continue  # the name belongs to another kind of entity (or is the client itself): never repurposed
        source_id, target_id = (entity.id, org.id) if rel_type == "COMPETITOR_OF" else (org.id, entity.id)
        rel = await session.scalar(select(Relationship).where(Relationship.source_entity_id == source_id, Relationship.target_entity_id == target_id,
                                                              Relationship.relationship_type == rel_type))
        if rel is None:
            rel = Relationship(source_entity_id=source_id, target_entity_id=target_id, relationship_type=rel_type, confidence=None,
                               verification_status="unverified")
            session.add(rel)
            await session.flush()
            created += 1
        keep.add(rel.id)
        detail = data.competitor_notes.get(label) if rel_type == "COMPETITOR_OF" else None
        note = f"{tag}@v{version}: {detail}" if detail else f"{tag}@v{version}: listed in the client profile"
        ev = next((e for r, e in existing if r.id == rel.id), None)
        if ev is None:
            session.add(RelationshipEvidence(relationship_id=rel.id, note=note))
        else:
            ev.note = note
    removed = 0
    for rel, _ev in existing:
        if rel.id not in keep:
            await session.delete(rel)
            removed += 1
    await session.flush()
    return {"created": created, "removed": removed}
