"""Re-match all existing articles with the updated entity matcher.

Clears existing EntityMention records and re-runs entity_matcher on all articles.
Useful when the entity matcher is updated or new aliases are added.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import dispose_engine, get_sessionmaker
from app.models import Article, EntityMention
from app.services.entity_matcher import match_entities_for_article


async def rematch_articles(session: AsyncSession) -> dict[str, int]:
    stats = {"cleared": 0, "rematched": 0, "new_mentions": 0}

    # Clear existing mentions
    result = await session.execute(delete(EntityMention))
    stats["cleared"] = result.rowcount

    # Get all articles
    articles = await session.scalars(select(Article))

    # Re-match each article
    for article in articles:
        mentions = await match_entities_for_article(session, article)
        stats["rematched"] += 1
        stats["new_mentions"] += len(mentions)

    await session.commit()
    return stats


async def main() -> None:
    maker = get_sessionmaker()
    async with maker() as session:
        stats = await rematch_articles(session)
    await dispose_engine()
    print("rematch_articles complete; stats:", stats)


if __name__ == "__main__":
    asyncio.run(main())
