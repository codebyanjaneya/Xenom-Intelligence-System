"""Recall recovery (PDF s.7 step 4, s.8): the ONLY route back for rejected items.

Rejected items are already stored with their reason. This path re-scores them against the client PROFILE (embedding similarity
when a provider is configured, otherwise the literal profile-term overlap) and queues high scorers for review in
``review_items``. A person accepts or dismisses; nothing is re-admitted automatically, semantic search never does it, and an
LLM adjudication (``llm_adjudication`` review items) can only recommend.

Hard negatives are deliberate exclusions and are never candidates. A candidate without a real publisher URL cannot be
accepted: a discovery wrapper would become provenance.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.clients import ENTITY_KEY_BY_CLIENT
from app.config.settings import get_settings
from app.models import ReviewItem
from app.models.content import Article
from app.repositories import articles as article_repo
from app.services import embeddings as emb
from app.services import profiles as prof
from app.services.chunking import chunk_article
from app.services.entity_matcher import attach_mention
from app.services.google_news_decoder import is_google_news_url
from app.services.markup import strip_markup
from app.services.taxonomy import label_article, load_profiles

CANDIDATE_TYPES = ("recall_candidate", "llm_adjudication")
LEXICAL_THRESHOLD = 0.5  # >= 3 distinct profile terms (hits / (hits + 3))


def profile_terms(profile: prof.ProfileData) -> list[str]:
    fields = (profile.strong_keywords, profile.aliases, profile.legal_names, profile.products, profile.industries, profile.sub_industries,
              profile.context_keywords, profile.executives, profile.technologies, profile.competitors, profile.research_interests)
    seen, out = set(), []
    for term in (t for field in fields for t in field):
        if len(term) >= 3 and term.casefold() not in seen:
            seen.add(term.casefold())
            out.append(term)
    return out


def profile_text(profile: prof.ProfileData) -> str:
    return f"{profile.description}. " + ", ".join(profile_terms(profile)[:60])


def lexical_similarity(text: str, profile: prof.ProfileData) -> tuple[float, list[str]]:
    hits = [t for t in profile_terms(profile) if re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", text, re.IGNORECASE)]
    return round(len(hits) / (len(hits) + 3), 3), hits[:8]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _evidence(article: Article) -> dict:
    try:
        return json.loads(article.score_reason or "{}")
    except ValueError:
        return {}


async def scan(session: AsyncSession, *, client: str | None = None, limit: int = 500) -> dict:
    """Score relevance-stage rejects against each (or one) client profile and queue the high scorers. Idempotent."""
    names = [prof.resolve(client)[1]] if client else list(ENTITY_KEY_BY_CLIENT)
    profiles = {n: (await prof.effective_profile(session, n))[1] for n in names}
    rows = [a for a in (await session.scalars(select(Article).where(Article.processing_status == "rejected").order_by(Article.id).limit(limit * 4))).all()
            if _evidence(a).get("stage") == "relevance" and _evidence(a).get("reason") != "HARD_NEGATIVE"][:limit]
    queued_keys = {(r.target_id, json.loads(r.reason or "{}").get("client")) for r in
                   (await session.scalars(select(ReviewItem).where(ReviewItem.object_type == "recall_candidate"))).all()}
    provider = emb.get_provider()
    method, vectors, profile_vecs = "lexical_profile", {}, {}
    if provider is not None and rows:
        try:
            texts = [f"{strip_markup(a.title) or ''}. {strip_markup(a.summary) or ''}" for a in rows]
            article_vecs, _p = await emb.embed_texts(session, texts, operation="embedding:recall")
            profile_vec_list, _p = await emb.embed_texts(session, [profile_text(profiles[n]) for n in names], operation="embedding:recall")
            vectors = {a.id: v for a, v in zip(rows, article_vecs)}
            profile_vecs = dict(zip(names, profile_vec_list))
            method = "embedding"
        except emb.EmbeddingUnavailable:
            method = "lexical_profile"
    threshold = get_settings().recall_similarity_threshold if method == "embedding" else LEXICAL_THRESHOLD
    queued, scored = Counter(), 0
    for article in rows:
        text = f"{strip_markup(article.title) or ''}. {strip_markup(article.summary) or ''}"
        for name in names:
            scored += 1
            if method == "embedding":
                similarity, terms = round(cosine(vectors[article.id], profile_vecs[name]), 3), []
            else:
                similarity, terms = lexical_similarity(text, profiles[name])
            if similarity < threshold or (article.id, name) in queued_keys:
                continue
            session.add(ReviewItem(object_type="recall_candidate", target_table="articles", target_id=article.id, status="open",
                                   reason=json.dumps({"client": name, "similarity": similarity, "method": method, "matched_profile_terms": terms,
                                                      "prior_reason": _evidence(article).get("reason"), "threshold": threshold,
                                                      "model": provider.model if method == "embedding" else None}, ensure_ascii=False)))
            queued[name] += 1
    await session.commit()
    return {"method": method, "candidates_scored": scored, "queued": dict(queued), "threshold": threshold,
            **({"detail": "no embedding provider: profile-term overlap was used"} if provider is None else {})}


async def list_candidates(session: AsyncSession, status: str = "open") -> list[dict]:
    items = (await session.scalars(select(ReviewItem).where(ReviewItem.object_type.in_(CANDIDATE_TYPES), ReviewItem.status == status)
                                   .order_by(ReviewItem.created_at.desc(), ReviewItem.id))).all()
    out = []
    for item in items:
        article = await session.get(Article, item.target_id) if item.target_id else None
        detail = json.loads(item.reason or "{}")
        out.append({"id": str(item.id), "type": item.object_type, "status": item.status, "article_id": str(item.target_id) if item.target_id else None,
                    "title": strip_markup(article.title) if article else None, "url": (article.canonical_url or article.url) if article else None,
                    "article_status": article.processing_status if article else None, **detail})
    return out


class RecoveryError(ValueError):
    pass


async def decide(session: AsyncSession, review_id, *, accept: bool, actor: str | None) -> dict:
    item = await session.get(ReviewItem, review_id)
    if item is None or item.object_type not in CANDIDATE_TYPES:
        raise LookupError("candidate not found")
    if item.status != "open":
        raise RecoveryError(f"candidate already {item.status}")
    if not accept:
        item.status = "dismissed"
        await session.commit()
        return {"status": "dismissed"}
    article = await session.get(Article, item.target_id)
    detail = json.loads(item.reason or "{}")
    client = detail.get("client")
    if article is None or client not in ENTITY_KEY_BY_CLIENT:
        raise RecoveryError("candidate has no article or client")
    if article.processing_status != "rejected":
        raise RecoveryError("article is not rejected")
    publisher_url = article.canonical_url
    if not publisher_url or is_google_news_url(publisher_url):
        raise RecoveryError("no publisher URL is stored for this article; a discovery wrapper cannot become provenance")
    duplicate = await article_repo.find_cross_source_duplicate(session, article_id=article.id, urls=[publisher_url] if publisher_url != article.url else [],
                                                                title=article.title, published_at=article.published_at)
    if duplicate is not None:
        raise RecoveryError(f"duplicate of accepted article {duplicate.id}")
    evidence = _evidence(article)
    article.processing_status, article.quality_gate_passed = "accepted", True
    article.score_reason = json.dumps({**evidence, "decision": "accepted", "stage": "accepted", "reason": None, "entity": ENTITY_KEY_BY_CLIENT[client],
                                       "matched_terms": detail.get("matched_profile_terms") or evidence.get("matched_terms", []),
                                       "recovered_by": {"review_item": str(item.id), "actor": actor, "similarity": detail.get("similarity"),
                                                        "method": detail.get("method") or "llm_adjudication", "prior_reason": evidence.get("reason")}},
                                      ensure_ascii=False, separators=(",", ":"))
    await attach_mention(session, article, ENTITY_KEY_BY_CLIENT[client], (detail.get("matched_profile_terms") or [None])[0])
    item.status = "resolved"
    await chunk_article(session, article)
    await label_article(session, article, profiles=await load_profiles(session))
    await session.commit()
    return {"status": "accepted", "article_id": str(article.id), "client": client}
