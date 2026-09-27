"""Read-only lexical search over stored, accepted articles (title + summary).

Layered, deterministic ordering (no index or migration; to_tsvector is computed per query):
  1. all query terms match (PostgreSQL full-text AND) ranks above partial matches;
  2. articles whose stored client entity is named in the query rank above those that only mention it;
  3. ts_rank_cd (title weighted above summary);
  4. recency, then id.
Partial matching (>= 2/3 of the distinct query lexemes) is used only for plain queries; queries that use
websearch operators (quotes, OR, -) stay strict. Stopword-only queries return nothing.
"""
from __future__ import annotations

import math
import re
import uuid
from datetime import datetime

from sqlalchemy import Integer, cast, desc, exists, func, literal_column, nullslast, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.clients import CLIENT_CONFIG
from app.models.content import Article
from app.models.entities import Entity, EntityMention
from app.models.sources import Source
from app.schemas.article_search import ArticleSearchHit, ArticleSearchResponse
from app.services.markup import strip_markup
from app.services.articles import _relevance_evidence

_CONFIG = "english"
SOURCE_CLASSES = ("news", "trusted_news", "official", "directory", "reference", "research", "forum")
TIERS = ("TIER_1", "TIER_2", "TIER_3", "TIER_4")


def suggested_client_filters(query: str) -> dict[str, list[str]]:
    """Clients named in the query text (canonical name or alias, whole word). Suggested to the caller, never applied."""
    named = []
    for name, cfg in CLIENT_CONFIG.items():
        terms = {name, cfg.canonical_name, *cfg.aliases}
        if any(re.search(r"\b" + re.escape(t) + r"\b", query, re.IGNORECASE) for t in terms if t and len(t) >= 3):
            named.append(name.lower())
    return {"client": sorted(named)} if named else {}
_MAX_TERMS = 8
_STOPWORDS = frozenset("the and for with from that this are was were has have had its into over about after new of in on to is at by an as it or be".split())


def matched_query_terms(query: str, *texts: str | None) -> list[str]:
    hay = " ".join(t for t in texts if t)
    terms = sorted({t for t in re.findall(r"\w+", query.lower()) if len(t) >= 2 and t not in _STOPWORDS})
    return [t for t in terms if re.search(r"\b" + re.escape(t), hay, re.IGNORECASE)]
_OPERATORS = re.compile(r'["]|(?:^|\s)-\S|\bor\b', re.IGNORECASE)


def min_terms_required(n: int) -> int:
    """Partial-match floor: all terms for n<=2, otherwise ceil(2n/3)."""
    return n if n <= 2 else math.ceil(2 * n / 3)


def _quote(lexeme: str) -> str:
    return "'" + lexeme.replace("'", "''").replace("\\", "\\\\") + "'"


async def _lexemes(session: AsyncSession, query: str) -> list[str]:
    rows = await session.execute(
        text("select lexeme from unnest(to_tsvector(:cfg, :q)) order by lexeme"), {"cfg": _CONFIG, "q": query}
    )
    return [r[0] for r in rows.all()][:_MAX_TERMS]


def apply_filters(statement, *, entity_key=None, source=None, tier=None, source_class=None, topic=None,
                  date_from=None, date_to=None, label=None, person=None, technology=None):
    """Filters before ranking (PDF s.8): client/entity, taxonomy label, source class and tier, date range, person, technology.
    Shared by the lexical and the vector retriever so both search exactly the same corpus."""
    from app.models.architecture import ArticleLabel, DocumentAuthor
    from app.config import taxonomy as taxonomy_cfg

    if entity_key:
        statement = statement.where(
            exists().where(
                EntityMention.article_id == Article.id,
                EntityMention.entity_id == Entity.id,
                Entity.canonical_key == entity_key.strip().lower(),
                Entity.entity_type == "ORG",
            )
        )
    if source:
        statement = statement.where(func.lower(Source.name) == source.strip().lower())
    if tier:
        if tier not in TIERS:
            raise ValueError(f"tier must be one of {TIERS}")
        statement = statement.where(Source.tier == tier)
    if source_class:
        if source_class not in SOURCE_CLASSES:
            raise ValueError(f"source_class must be one of {SOURCE_CLASSES}")
        statement = statement.where(Article.score_reason.op("~")(r'"source_type"\s*:\s*"' + source_class + '"'))
    if topic:
        statement = statement.where(func.lower(func.coalesce(Article.topic, "")).contains(topic.strip().lower(), autoescape=True))
    if label:
        if label not in taxonomy_cfg.LABELS:
            raise ValueError(f"label must be one of {taxonomy_cfg.LABELS}")
        statement = statement.where(exists().where(ArticleLabel.article_id == Article.id, ArticleLabel.label == label))
    if person:
        needle = " ".join(person.split())
        statement = statement.where(or_(
            func.lower(func.coalesce(Article.author, "")).contains(needle.lower(), autoescape=True),
            exists().where(DocumentAuthor.article_id == Article.id, DocumentAuthor.entity_id == Entity.id,
                           func.lower(Entity.canonical_name).contains(needle.lower(), autoescape=True)),
        ))
    if technology:
        statement = statement.where(literal_column("articles.search_vector").op("@@")(func.websearch_to_tsquery(_CONFIG, technology)))
    if date_from:
        statement = statement.where(Article.published_at >= date_from)
    if date_to:
        statement = statement.where(Article.published_at <= date_to)
    return statement


async def search_articles(
    session: AsyncSession,
    query: str,
    *,
    entity_key: str | None = None,
    source: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    tier: str | None = None,
    source_class: str | None = None,
    topic: str | None = None,
    label: str | None = None,
    person: str | None = None,
    technology: str | None = None,
    limit: int = 20,
) -> ArticleSearchResponse:
    query = " ".join(query.split())
    if not query:
        raise ValueError("query must not be blank")
    document = literal_column("articles.search_vector")  # generated column: title (A) + summary (B), GIN-indexed
    ts_query = func.websearch_to_tsquery(_CONFIG, query)  # strict: every term must match
    lexemes = [] if _OPERATORS.search(query) else await _lexemes(session, query)
    if len(lexemes) > 1:
        term_hits = [cast(document.op("@@")(func.to_tsquery("simple", _quote(x))), Integer) for x in lexemes]
        matched = term_hits[0]
        for hit in term_hits[1:]:
            matched = matched + hit
        rank_query = func.to_tsquery("simple", " | ".join(_quote(x) for x in lexemes))
        matches = or_(document.op("@@")(ts_query), matched >= min_terms_required(len(lexemes)))
    else:
        rank_query, matches = ts_query, document.op("@@")(ts_query)
    full_match = document.op("@@")(ts_query)
    score = func.ts_rank_cd(document, rank_query).label("score")
    tokens = sorted(set(re.findall(r"\w+", query.lower())))
    client_named = exists().where(
        EntityMention.article_id == Article.id,
        EntityMention.entity_id == Entity.id,
        Entity.entity_type == "ORG",
        Entity.canonical_key.in_(tokens),
    )

    statement = (
        select(Article, Source.name, Source.source_type, Source.tier, score)
        .outerjoin(Source, Article.source_id == Source.id)
        .where(Article.processing_status == "accepted", Article.quality_gate_passed.is_(True))
        .where(matches)
    )
    statement = apply_filters(
        statement, entity_key=entity_key, source=source, tier=tier, source_class=source_class, topic=topic,
        date_from=date_from, date_to=date_to, label=label, person=person, technology=technology,
    )
    statement = statement.order_by(
        desc(full_match), desc(client_named), desc("score"), nullslast(desc(Article.published_at)), Article.id
    ).limit(limit)

    hits = []
    for article, source_name, source_type, source_tier, hit_score in (await session.execute(statement)).all():
        evidence = _relevance_evidence(article.score_reason)
        hits.append(ArticleSearchHit(
            article_id=article.id,
            title=strip_markup(article.title),
            summary=strip_markup(article.summary),
            url=article.canonical_url or article.url,  # publisher URL; the stored url may be a discovery wrapper
            published_at=article.published_at,
            client=(evidence or {}).get("entity"),
            source=source_name,
            source_class=(evidence or {}).get("source_type") or source_type,
            tier=source_tier,
            matched_terms=matched_query_terms(query, article.title, article.summary),
            relevance=evidence,
            score=round(float(hit_score), 6),
        ))
    return ArticleSearchResponse(query=query, count=len(hits), results=hits,
                                 suggested_filters={} if entity_key else suggested_client_filters(query))
