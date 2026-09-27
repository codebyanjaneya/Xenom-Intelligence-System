"""Seed entity aliases for better matching.

Adds common aliases/abbreviations for technology entities to improve article matching.
Idempotent: existing aliases are not duplicated.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import dispose_engine, get_sessionmaker
from app.models import Entity, EntityAlias

# Map entity canonical_name -> list of aliases
ENTITY_ALIASES = {
    "Artificial Intelligence": ["AI", "AGI"],
    "Machine Learning": ["ML"],
    "Cloud Computing": ["cloud", "cloud services"],
    "Semiconductors": ["semiconductor", "chip", "chips"],
    "Internet of Things": ["IoT"],
    "Blockchain": ["blockchain", "crypto"],
    "Cybersecurity": ["cybersecurity", "security", "breach"],
}


async def seed_aliases(session: AsyncSession) -> dict[str, int]:
    created = {"aliases": 0}

    for entity_name, aliases in ENTITY_ALIASES.items():
        # Find entity by canonical_name
        entity = await session.scalar(
            select(Entity).where(Entity.canonical_name == entity_name)
        )
        if entity is None:
            print(f"Warning: Entity not found: {entity_name}")
            continue

        # Add each alias if not already present
        for alias_text in aliases:
            existing = await session.scalar(
                select(EntityAlias).where(
                    EntityAlias.entity_id == entity.id,
                    EntityAlias.alias == alias_text,
                )
            )
            if existing is None:
                session.add(EntityAlias(entity_id=entity.id, alias=alias_text))
                created["aliases"] += 1

    await session.commit()
    return created


async def main() -> None:
    maker = get_sessionmaker()
    async with maker() as session:
        created = await seed_aliases(session)
    await dispose_engine()
    print("seed_aliases complete; newly created rows:", created)


if __name__ == "__main__":
    asyncio.run(main())
