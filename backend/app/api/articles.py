from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Article, Entity, EntityMention, SavedArticle, Source, User

from app.api.deps import get_current_user
from app.api.deps import accessible_clients, require_client_access
from app.db.session import get_session
from app.schemas.article import ArticleOut
from app.services.articles import get_articles
from app.services.domain_intelligence import diverse_feed
from app.services.articles import _relevance_evidence
from app.services.markup import strip_markup

router = APIRouter(prefix="/articles", tags=["articles"])


async def _saved_article_out(session: AsyncSession, article: Article) -> ArticleOut:
    source = await session.get(Source, article.source_id) if article.source_id else None
    names = (await session.scalars(select(Entity.canonical_name).join(EntityMention, EntityMention.entity_id == Entity.id)
              .where(EntityMention.article_id == article.id, Entity.entity_type == "ORG").order_by(Entity.canonical_name))).all()
    return ArticleOut(id=article.id, title=strip_markup(article.title), url=article.canonical_url or article.url,
                      canonical_url=article.canonical_url, image_url=article.image_url, source_name=source.name if source else None,
                      published_at=article.published_at, fetched_at=article.fetched_at, summary=strip_markup(article.summary),
                      source_id=article.source_id, entity_names=list(names), topic=article.topic,
                      quality_score=article.relevance_score, relevance=_relevance_evidence(article.score_reason))


@router.get("/saved", response_model=list[ArticleOut])
async def list_saved_articles(current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> list[ArticleOut]:
    rows = (await session.scalars(select(Article).join(SavedArticle, SavedArticle.article_id == Article.id)
                                  .where(SavedArticle.user_id == current_user.id)
                                  .order_by(SavedArticle.created_at.desc()))).all()
    return [await _saved_article_out(session, article) for article in rows]


@router.put("/{article_id}/saved", response_model=ArticleOut)
async def save_article(article_id: uuid.UUID, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> ArticleOut:
    article = await session.get(Article, article_id)
    if article is None or article.processing_status != "accepted":
        raise HTTPException(status_code=404, detail="Article not found")
    exists = await session.scalar(select(SavedArticle).where(SavedArticle.user_id == current_user.id, SavedArticle.article_id == article_id))
    if exists is None:
        session.add(SavedArticle(user_id=current_user.id, article_id=article_id))
        await session.commit()
    return await _saved_article_out(session, article)


@router.delete("/{article_id}/saved", status_code=204)
async def unsave_article(article_id: uuid.UUID, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_session)) -> None:
    await session.execute(delete(SavedArticle).where(SavedArticle.user_id == current_user.id, SavedArticle.article_id == article_id))
    await session.commit()


@router.get("", response_model=list[ArticleOut], dependencies=[Depends(get_current_user)])
async def list_article_records(
    source_id: uuid.UUID | None = None,
    source_name: str | None = None,
    entity_key: str | None = None,
    resolve_images: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[ArticleOut]:
    scoped_clients = await accessible_clients(session, current_user)
    if entity_key:
        entity_key = (await require_client_access(session, current_user, entity_key)).lower()
    elif current_user.role != "ADMIN":
        if not scoped_clients:
            return []
        results = []
        for client in scoped_clients:
            results.extend(await get_articles(session, source_id=source_id, source_name=source_name,
                                              entity_key=client.lower(), limit=limit, offset=0,
                                              resolve_images=resolve_images))
        return results[offset:offset + limit]
    return await get_articles(
        session,
        source_id=source_id,
        source_name=source_name,
        entity_key=entity_key,
        limit=limit,
        offset=offset,
        resolve_images=resolve_images,
    )


@router.get("/feed", response_model=list[ArticleOut], dependencies=[Depends(get_current_user)])
async def diverse_article_feed(
    limit: int = Query(default=24, ge=1, le=100),
    max_per_publisher: int = Query(default=3, ge=1, le=20),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[ArticleOut]:
    """Global feed with client and publisher diversity (round-robin across clients), not just the newest N."""
    return await diverse_feed(session, limit, max_per_publisher, allowed_clients=await accessible_clients(session, current_user))


@router.get("/{article_id}/detail", dependencies=[Depends(get_current_user)])
async def article_detail(article_id: uuid.UUID, session: AsyncSession = Depends(get_session), current_user: User = Depends(get_current_user)) -> dict:
    """One stored item with its provenance and the stored relevance decision (PDF s.12 Detail): source, tier, class, dates,
    publisher URL, decision + reason, labels, authors, claims, chunks. The Google discovery URL is never returned."""
    import json

    from fastapi import HTTPException, status
    from sqlalchemy import select

    from app.models import Claim, ClaimSource, Entity, EntityMention, Source
    from app.models.architecture import ArticleLabel, DocumentAuthor, DocumentChunk
    from app.models.content import Article
    from app.services.google_news_decoder import is_google_news_url
    from app.services.markup import strip_markup

    article = await session.get(Article, article_id)
    if article is None or article.processing_status not in ("accepted", "rejected"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")
    source = await session.get(Source, article.source_id) if article.source_id else None
    try:
        decision = json.loads(article.score_reason) if article.score_reason else None
    except ValueError:
        decision = None
    url = article.canonical_url or (None if is_google_news_url(article.url) else article.url)
    labels = (await session.scalars(select(ArticleLabel).where(ArticleLabel.article_id == article.id).order_by(ArticleLabel.label, ArticleLabel.method))).all()
    clients = (await session.scalars(select(Entity.canonical_name).join(EntityMention, EntityMention.entity_id == Entity.id)
                                     .where(EntityMention.article_id == article.id, Entity.entity_type == "ORG").order_by(Entity.canonical_name))).all()
    if current_user.role != "ADMIN" and not ({c.casefold() for c in clients} & {c.casefold() for c in await accessible_clients(session, current_user)}):
        raise HTTPException(status_code=403, detail="You do not have access to this article")
    authors = (await session.execute(select(Entity.canonical_name, DocumentAuthor.affiliation, DocumentAuthor.position).join(Entity, Entity.id == DocumentAuthor.entity_id)
                                     .where(DocumentAuthor.article_id == article.id).order_by(DocumentAuthor.position))).all()
    chunks = (await session.execute(select(DocumentChunk.field, DocumentChunk.chunk_index, DocumentChunk.char_start, DocumentChunk.char_end, DocumentChunk.embedding_model)
                                    .where(DocumentChunk.article_id == article.id).order_by(DocumentChunk.field, DocumentChunk.chunk_index))).all()
    claims = (await session.execute(select(Claim.id, Claim.claim_text, Claim.verification_status).join(ClaimSource, ClaimSource.claim_id == Claim.id)
                                    .where(ClaimSource.article_id == article.id).order_by(Claim.created_at))).all()
    return {
        "article_id": str(article.id), "title": strip_markup(article.title), "summary": strip_markup(article.summary), "url": url,
        "published_at": article.published_at, "fetched_at": article.fetched_at, "author": article.author,
        "status": article.processing_status, "relevance_score": article.relevance_score,
        "source": {"name": source.name, "tier": source.tier, "source_class": source.source_class, "reliability_score": source.reliability_score} if source else None,
        "decision": decision, "clients": list(clients),
        "labels": [{"label": l.label, "method": l.method, "confidence": l.confidence, "evidence": l.evidence} for l in labels],
        "authors": [{"name": n, "affiliation": a, "position": p} for n, a, p in authors],
        "claims": [{"claim_id": str(i), "claim": t, "status": s} for i, t, s in claims],
        "chunks": [{"field": f, "index": i, "start": s, "end": e, "embedded_with": m} for f, i, s, e, m in chunks],
        **({"url_note": "No publisher URL is stored for this item (only a discovery link), so none is shown."} if url is None else {}),
    }
