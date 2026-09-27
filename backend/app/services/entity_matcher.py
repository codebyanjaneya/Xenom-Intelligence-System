"""
Entity matcher: links articles to entities using strict multi-signal matching.

Replaces simple substring matching with confidence-based multi-signal approach.
- Exact legal names required for match
- Multiple signals combined for confidence score
- Exclusion rules prevent false positives
- Context validation ensures relevance
"""

from __future__ import annotations

import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import Article, Entity, EntityMention
from app.services.strict_entity_matcher import StrictEntityMatcher

logger = logging.getLogger(__name__)

# EntityConfig key -> Entity.canonical_key in the database
ENTITY_KEY_TO_CANONICAL_KEY = {
    "VIKAS": "vikas",
    "ACE": "ace",
    "ISM": "ism",
    "JCB": "jcb",
    "Definedge": "definedge",
    "VIPS": "vips",
}


async def match_entities_for_article(
    session: AsyncSession, article: Article
) -> list[EntityMention]:
    """
    Match article against entities using strict multi-signal matching.

    Uses the StrictEntityMatcher for high-confidence entity matching.
    Only creates EntityMention for matches that pass:
    - Confidence threshold (35+ points)
    - Exclusion rules
    - Context validation

    Prevents false positives like:
    - Vidya Vikas → NOT matched as VIKAS
    - Ace Turtle → NOT matched as ACE
    - Generic "vikas" → NOT matched without context
    - Vikas Infrastructure → NOT matched (excluded)

    Returns list of created EntityMention records (empty if no matches).
    """
    if not article.title and not article.summary:
        return []

    # Get only ORG-type entities (client organizations)
    entities_result = await session.scalars(select(Entity).where(Entity.entity_type == "ORG"))
    entities = list(entities_result)

    if not entities:
        return []

    # Initialize strict matcher
    matcher = StrictEntityMatcher()

    # Get article content (extract source domain if available)
    title = article.title or ""
    summary = article.summary or ""
    source_domain = ""  # Could be extracted from source if available

    # Get match results (includes rejected matches with reasons)
    match_results = matcher.match_entities_for_article(
        title=title,
        summary=summary,
        source_domain=source_domain,
    )

    # Log rejections for debugging/monitoring
    for result in match_results:
        if not result.matched and result.rejection_reason:
            logger.debug(
                f"Entity match rejected: {result.entity_key} - {result.rejection_reason}",
                extra={
                    "entity_key": result.entity_key,
                    "reason": result.rejection_reason,
                    "matched_text": result.matched_text,
                    "confidence_score": result.confidence_score,
                }
            )

    # Create EntityMention objects only for accepted matches
    mentions: list[EntityMention] = []

    for result in match_results:
        if not result.matched:
            continue

        # Find entity by canonical_key (matching entity_key from config)
        canonical_key = ENTITY_KEY_TO_CANONICAL_KEY.get(result.entity_key, result.entity_key.casefold())
        if not canonical_key:
            continue

        # Find matching entity in database
        entity = next(
            (e for e in entities if e.canonical_key == canonical_key),
            None
        )

        if not entity:
            logger.warning(
                f"Entity not found for key: {canonical_key}",
                extra={"entity_key": result.entity_key}
            )
            continue

        # Check if mention already exists
        existing = await session.scalar(
            select(EntityMention).where(
                EntityMention.entity_id == entity.id,
                EntityMention.article_id == article.id,
            )
        )

        if existing is None:
            mention = EntityMention(
                entity_id=entity.id,
                article_id=article.id,
                mention_text=result.matched_text or canonical_key,
            )
            session.add(mention)
            mentions.append(mention)

            logger.debug(
                f"Entity match accepted: {canonical_key}",
                extra={
                    "entity_key": result.entity_key,
                    "confidence_score": result.confidence_score,
                    "signals": len(result.signals),
                }
            )

    if mentions:
        await session.flush()

    return mentions


async def attach_mention(
    session: AsyncSession, article: Article, entity_key: str, mention_text: str | None
) -> EntityMention | None:
    """Create (idempotently) the EntityMention for a relevance-accepted client."""
    canonical_key = ENTITY_KEY_TO_CANONICAL_KEY.get(entity_key, entity_key.casefold())
    if not canonical_key.strip():
        return None
    entity = await session.scalar(
        select(Entity).where(Entity.canonical_key == canonical_key, Entity.entity_type == "ORG")
    )
    if entity is None:
        logger.warning("Entity not found for key: %s", canonical_key)
        return None
    existing = await session.scalar(
        select(EntityMention).where(EntityMention.entity_id == entity.id, EntityMention.article_id == article.id)
    )
    if existing is not None:
        return existing
    mention = EntityMention(entity_id=entity.id, article_id=article.id, mention_text=mention_text or canonical_key)
    session.add(mention)
    await session.flush()
    return mention
