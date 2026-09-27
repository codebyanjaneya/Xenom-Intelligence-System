"""Rule-first multi-label taxonomy (PDF s.9). An item can carry several labels and is never forced into one.

Each label row records the label, the method (rule / embedding / llm / human), a confidence and the evidence. Rules are
literal: a label needs a term found in the stored title/summary/text, a stored relevance decision, a source class or a
profile entry. Embedding and LLM labels use the same table and are written by their own services when those are configured.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import taxonomy as cfg
from app.config.clients import CLIENT_CONFIG
from app.config.intelligence_themes import TECH_THEMES
from app.models.architecture import ArticleLabel
from app.models.content import Article
from app.models.sources import Source
from app.services import profiles as prof
from app.services.markup import strip_markup


@lru_cache(maxsize=2048)
def _pattern(term: str) -> re.Pattern[str]:
    return re.compile(r"(?<!\w)" + re.escape(term.strip()) + r"(?!\w)", re.IGNORECASE)


def _hits(text: str, terms) -> list[str]:
    found: list[str] = []
    for term in terms:
        if term and _pattern(term).search(text) and term.casefold() not in {f.casefold() for f in found}:
            found.append(term)
    return found


def _confidence(n_terms: int, tier: str | None) -> float:
    return round(min(0.95, 0.45 + 0.1 * n_terms) * cfg.TIER_CONFIDENCE_FACTOR.get(tier or "TIER_3", 0.75), 2)


def _relevance(article: Article) -> dict:
    try:
        return json.loads(article.score_reason) if article.score_reason else {}
    except ValueError:
        return {}


def compute_labels(article: Article, *, tier: str | None, source_class: str | None,
                   profiles: dict[str, prof.ProfileData], clients: list[str]) -> list[dict]:
    """Deterministic rule labels for one stored article. ``clients`` = the clients it was accepted for."""
    title, summary = strip_markup(article.title) or "", strip_markup(article.summary) or ""
    text = f"{title}. {summary}"
    labels: list[dict] = []

    def add(label: str, terms: list[str], *, confidence: float | None = None, basis: str, extra: dict | None = None) -> None:
        field = "title" if any(_pattern(t).search(title) for t in terms) else "summary"
        labels.append({"label": label, "confidence": confidence if confidence is not None else _confidence(len(terms), tier),
                       "evidence": {"terms": terms[:8], "field": field, "basis": basis, **(extra or {})}})

    evidence = _relevance(article)
    if evidence.get("decision") == "accepted" and clients:
        add("company", [str(t) for t in (evidence.get("matched_terms") or clients)][:5],
            confidence=round(float(evidence.get("relevance_score") or 0.0), 2), basis="stored relevance decision",
            extra={"clients": clients})

    competitors = sorted({c for client in clients for c in profiles[client].competitors})
    if (hit := _hits(text, competitors)):
        add("competitor", hit, basis="competitor named in the client profile")
    industries = sorted({i for client in clients for i in [*profiles[client].industries, *profiles[client].sub_industries]})
    if clients and (hit := _hits(text, industries)):
        add("industry", hit, basis="industry term from the client profile, with client context")
    technologies = sorted({t for client in clients for t in [*profiles[client].technologies, *profiles[client].research_interests]})
    tech_hits = _hits(text, technologies)
    theme_hits = [t.label for t in TECH_THEMES if re.search(r"(?<!\w)(?:" + t.pattern + r")(?!\w)", text, re.IGNORECASE) and t.key != "ai_ml_general"]
    if tech_hits or theme_hits:
        add("technology", [*tech_hits, *theme_hits], basis="technology named in the profile or matching a tracked theme")
    models = sorted({m for client in clients for m in profiles[client].ai_model_interests} | set(cfg.AI_MODEL_TERMS))
    if (hit := _hits(text, models)):
        add("ai_model", hit, basis="model-name dictionary")
    if source_class == "research" or (hit := _hits(text, cfg.RESEARCH_MARKERS)):
        add("research", [f"source class: research"] if source_class == "research" else hit,
            basis="research source class" if source_class == "research" else "paper marker")
    signals = {name: found for name, terms in cfg.OPPORTUNITY_SIGNALS.items() if (found := _hits(text, terms))}
    if signals:
        add("opportunity", sorted(signals), basis="opportunity-signal detector (a signal, not a confirmed opportunity)",
            extra={"signals": {k: v[:3] for k, v in sorted(signals.items())}, "confirmed": False})
    if (hit := _hits(text, cfg.PARTNERSHIP_TERMS)):
        add("partnership", hit, basis="partnership terms")
    if (hit := _hits(text, cfg.PRODUCT_BUSINESS_TERMS)):
        add("product_business_movement", hit, basis="launch / expansion / order terms")
    if (hit := _hits(text, cfg.REGULATORY_TERMS if source_class == "official" else cfg.REGULATORY_TERMS[:5])):
        add("regulatory_policy", hit, basis="government or policy terms")
    if (hit := _hits(text, cfg.FINANCIAL_TERMS)):
        add("market_financial_signal", hit, basis="financial terms")
    return labels


def research_topic_terms(title: str | None, summary: str | None, profiles: dict[str, prof.ProfileData] | None) -> list[str]:
    """Literal research/technology topics a research-class document states (profile interests + tracked technology themes).
    Used only to admit a research-source document that no client rule accepted; empty means it is not admitted."""
    text = f"{strip_markup(title) or ''}. {strip_markup(summary) or ''}"
    interests = sorted({t for p in (profiles or {}).values() for t in [*p.research_interests, *p.technologies, *p.ai_model_interests]})
    found = _hits(text, interests)
    found += [t.label for t in TECH_THEMES if t.key != "ai_ml_general" and re.search(r"(?<!\w)(?:" + t.pattern + r")(?!\w)", text, re.IGNORECASE)]
    return found


async def _clients_of(session: AsyncSession, article: Article) -> list[str]:
    from app.models.entities import Entity, EntityMention
    keys = (await session.scalars(select(Entity.canonical_key).join(EntityMention, EntityMention.entity_id == Entity.id)
                                  .where(EntityMention.article_id == article.id, Entity.entity_type == "ORG"))).all()
    by_key = {n.lower(): n for n in CLIENT_CONFIG}
    return sorted(by_key[k] for k in keys if k in by_key)


async def load_profiles(session: AsyncSession) -> dict[str, prof.ProfileData]:
    return {name: (await prof.effective_profile(session, name))[1] for name in CLIENT_CONFIG}


async def label_article(session: AsyncSession, article: Article, *, profiles: dict[str, prof.ProfileData] | None = None) -> list[str]:
    """(Re)write the rule labels of one article. Returns the label names."""
    profiles = profiles or await load_profiles(session)
    source = await session.get(Source, article.source_id) if article.source_id else None
    evidence = _relevance(article)
    computed = compute_labels(article, tier=source.tier if source else None,
                              source_class=(evidence.get("source_type") or (source.source_class if source else None)),
                              profiles=profiles, clients=await _clients_of(session, article))
    await session.execute(delete(ArticleLabel).where(ArticleLabel.article_id == article.id, ArticleLabel.method == "rule"))
    for row in computed:
        session.add(ArticleLabel(article_id=article.id, label=row["label"], method="rule", confidence=row["confidence"],
                                 evidence=row["evidence"], taxonomy_version=cfg.TAXONOMY_VERSION))
    await session.flush()
    return [r["label"] for r in computed]


async def backfill(session: AsyncSession, *, limit: int = 5000, only_unlabelled: bool = True) -> dict:
    """Explicit, admin-invoked labelling of stored accepted articles. Never runs implicitly."""
    profiles = await load_profiles(session)
    stmt = select(Article).where(Article.processing_status == "accepted").order_by(Article.id).limit(limit)
    if only_unlabelled:
        stmt = stmt.where(~select(ArticleLabel.id).where(ArticleLabel.article_id == Article.id, ArticleLabel.method == "rule").exists())
    counts: dict[str, int] = {}
    articles = (await session.scalars(stmt)).all()
    for article in articles:
        for name in await label_article(session, article, profiles=profiles):
            counts[name] = counts.get(name, 0) + 1
    await session.commit()
    return {"articles_labelled": len(articles), "labels": counts}
