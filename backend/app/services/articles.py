from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import Article
from app.repositories.articles import list_articles
from app.schemas.article import ArticleOut
from app.services.markup import strip_markup
from app.services.og_image import extract_og_image

logger = logging.getLogger(__name__)


async def resolve_article_image(
    session: AsyncSession,
    article: Article,
) -> str | None:
    """Opt-in read-time fallback: look up a real publisher image for an article that has none.

    Ingestion already resolves and validates images for accepted articles, so this only
    matters for legacy rows. Google-hosted images are never stored or returned.
    """
    if article.image_url and not any(
        bad in article.image_url for bad in ("googleusercontent.com", "news.google.com", "gstatic.com")
    ):
        return article.image_url

    image = await extract_og_image(article.url)
    if image:
        article.image_url = image
        await session.execute(update(Article).where(Article.id == article.id).values(image_url=image))
        await session.commit()
    return image


def _relevance_evidence(score_reason: str | None) -> dict | None:
    """``score_reason`` holds the pipeline's JSON decision for pipeline-ingested articles."""
    if not score_reason or not score_reason.startswith("{"):
        return None
    try:
        return json.loads(score_reason)
    except ValueError:
        return None


async def get_articles(
    session: AsyncSession,
    *,
    source_id: uuid.UUID | None = None,
    source_name: str | None = None,
    entity_key: str | None = None,
    limit: int = 50,
    offset: int = 0,
    resolve_images: bool = False,
) -> list[ArticleOut]:
    rows = await list_articles(
        session,
        source_id=source_id,
        source_name=source_name,
        entity_key=entity_key,
        limit=limit,
        offset=offset,
    )

    results = []
    for article, source_name_value, entity_names_value in rows:
        image_url = article.image_url
        if resolve_images and not image_url:
            image_url = await resolve_article_image(session, article)

        results.append(ArticleOut(
            id=article.id,
            title=strip_markup(article.title),
            url=article.canonical_url or article.url,  # publisher URL; the stored url may be a discovery wrapper
            canonical_url=article.canonical_url,
            image_url=image_url,
            source_name=source_name_value,
            published_at=article.published_at,
            fetched_at=article.fetched_at,
            summary=strip_markup(article.summary),
            source_id=article.source_id,
            entity_names=entity_names_value,
            topic=article.topic,
            quality_score=article.relevance_score,
            relevance=_relevance_evidence(article.score_reason),
        ))

    return results