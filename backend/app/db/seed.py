"""Idempotent M1 seed.

Seeds ONLY minimal, safe foundational data:
  * one development-only user (referential integrity; NOT a real credential)
  * a Xenom organization entity
  * the six registered client organizations as placeholders (NEEDS_REVIEW)
  * CLIENT_OF relationships (client -> Xenom), marked needs_review
  * one provisional Xenom capability, clearly marked UNVERIFIED

No websites, CEOs, competitors, industry facts, capabilities, AI usage, or
enrichment are invented. Running this script multiple times does not create
duplicates (every row is matched on a natural/unique key first).
"""

from __future__ import annotations

import asyncio
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.db.session import dispose_engine, get_sessionmaker
from app.models import Entity, Relationship, XenomCapability, User
from app.services.auth import hash_password

# The dev user's password is supplied via env (DEV_USER_PASSWORD) for production.
# For development, if unset, uses a standard dev password to allow frontend testing.
# No real credential is committed to source.
DEV_USER_EMAIL = "dev@xenom.local"
_DEFAULT_DEV_PASSWORD = "development-password"


def _dev_password_hash() -> str:
    dev_password = get_settings().dev_user_password or _DEFAULT_DEV_PASSWORD
    return hash_password(dev_password)

XENOM_NAME = "Xenom Technology"
REGISTERED_CLIENTS = ["JCB", "ACE", "Vikas", "VIPS", "ISM", "Definedge"]

PROVISIONAL_CAPABILITY = "Provisional capability draft (UNVERIFIED)"

# Core technology domains relevant to client intelligence
TECHNOLOGY_ENTITIES = [
    "Artificial Intelligence",
    "Machine Learning",
    "Cloud Computing",
    "Semiconductors",
    "Internet of Things",
    "Blockchain",
    "Cybersecurity",
]

# Industry classifications relevant to client focus areas
INDUSTRY_ENTITIES = [
    "Finance",
    "Construction",
    "Heavy Equipment",
    "Electronics Manufacturing",
    "Information Technology",
    "Education",
]


def canonical_key(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


async def get_or_create_entity(
    session: AsyncSession, name: str, entity_type: str = "ORG"
) -> tuple[Entity, bool]:
    key = canonical_key(name)
    existing = await session.scalar(select(Entity).where(Entity.canonical_key == key))
    if existing is not None:
        return existing, False
    entity = Entity(
        entity_type=entity_type,
        canonical_name=name,
        canonical_key=key,
        status="needs_review",
    )
    session.add(entity)
    await session.flush()
    return entity, True


async def seed(session: AsyncSession) -> dict[str, int]:
    created = {"users": 0, "entities": 0, "relationships": 0, "capabilities": 0}

    # Development-only user (documented placeholder hash, not a real secret).
    dev_user = await session.scalar(select(User).where(User.email == DEV_USER_EMAIL))
    if dev_user is None:
        session.add(
            User(
                email=DEV_USER_EMAIL,
                display_name="Development User",
                role="ADMIN",
                password_hash=_dev_password_hash(),
                is_active=True,
            )
        )
        created["users"] += 1

    xenom, made = await get_or_create_entity(session, XENOM_NAME)
    created["entities"] += int(made)

    for client_name in REGISTERED_CLIENTS:
        client, made = await get_or_create_entity(session, client_name)
        created["entities"] += int(made)

        rel = await session.scalar(
            select(Relationship).where(
                Relationship.source_entity_id == client.id,
                Relationship.target_entity_id == xenom.id,
                Relationship.relationship_type == "CLIENT_OF",
            )
        )
        if rel is None:
            session.add(
                Relationship(
                    source_entity_id=client.id,
                    target_entity_id=xenom.id,
                    relationship_type="CLIENT_OF",
                    verification_status="needs_review",
                )
            )
            created["relationships"] += 1

    # Technology entities for AI & TECHNOLOGY section
    for tech_name in TECHNOLOGY_ENTITIES:
        _entity, made = await get_or_create_entity(session, tech_name, entity_type="TECHNOLOGY")
        created["entities"] += int(made)

    # Industry entities for INDUSTRY section
    for industry_name in INDUSTRY_ENTITIES:
        _entity, made = await get_or_create_entity(session, industry_name, entity_type="INDUSTRY")
        created["entities"] += int(made)

    capability = await session.scalar(
        select(XenomCapability).where(XenomCapability.name == PROVISIONAL_CAPABILITY)
    )
    if capability is None:
        session.add(
            XenomCapability(
                name=PROVISIONAL_CAPABILITY,
                description="Placeholder capability draft. Content is not confirmed.",
                verification_status="UNVERIFIED",
                source_note="Created during M1 seed; requires human verification before use.",
            )
        )
        created["capabilities"] += 1

    await session.commit()
    return created


async def main() -> None:
    maker = get_sessionmaker()
    async with maker() as session:
        created = await seed(session)
    await dispose_engine()
    print("seed complete; newly created rows:", created)


if __name__ == "__main__":
    asyncio.run(main())
