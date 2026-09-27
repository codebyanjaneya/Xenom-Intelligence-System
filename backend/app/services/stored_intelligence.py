"""DB-backed intelligence over stored articles: search -> verbatim evidence -> entity context.

Deterministic, no fixtures, no LLM. Everything returned is read from stored rows:
  * entities  = rows already linked in `entity_mentions` for the articles search returned;
  * evidence  = verbatim spans of the stored article text that contain that entity's mention text
                (entities with no such span are dropped: the shared Entity schema requires evidence);
  * events    = stored sentences naming a linked entity plus an explicit action cue (services/stored_events.py);
                the sentence is the event title and its only evidence;
  * relationships = INVOLVES(entity, event) only for entities named in that same sentence;
  * timeline  = events ordered by the date the article's sentence states (event_time) or else by publication time
                (publication_fallback); publication time is never presented as the event date;
  * insights / opportunities / alerts = deterministic rules over the events above (services/derived_intelligence.py).
"""
from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from types import SimpleNamespace

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import Article
from app.models.entities import Entity as EntityRow
from app.models.entities import EntityMention
from app.schemas.intelligence import (
    Entity, EntityEventRelationship, EntityType, Event, Evidence, IntelligenceRequest, IntelligenceResponse,
    TimelineEntry,
)
from app.schemas.derived import Alert, Insight, Opportunity
from app.services.article_evidence import find_spans, search_with_evidence
from app.services.derived_intelligence import derive_alerts, derive_insights, derive_opportunities
from app.services.recommendations import match_capabilities
from app.services.stored_events import Occurrence, extract_events, merge_occurrences

PROVIDER = "stored_articles"
_VALID_TYPES = {t.value for t in EntityType}


class StoredEntity(Entity):
    article_ids: list[uuid.UUID]
    article_count: int


class ArticleContext(BaseModel):
    article_id: uuid.UUID
    url: str
    title: str | None
    client: str | None
    source: str | None
    source_class: str | None
    published_at: datetime | None
    score: float
    relevance: dict | None


class StoredEvent(Event):
    event_id: str  # same value as event_key (stable hash of article, field, offsets and type)
    entity_keys: list[str]  # registered entities that precede the action cue in the evidence sentence
    article_ids: list[uuid.UUID]  # every stored article that states this event (evidence has one item per source)
    source_count: int


class StoredTimelineEntry(TimelineEntry):
    article_ids: list[uuid.UUID]


class StoredIntelligenceResponse(IntelligenceResponse):
    entities: list[StoredEntity]
    events: list[StoredEvent]
    timeline: list[StoredTimelineEntry]
    articles: list[ArticleContext]
    raw_event_count: int = 0  # extracted sentences before cross-article merging
    insights: list[Insight] = []
    opportunities: list[Opportunity] = []
    alerts: list[Alert] = []


def _evidence(article: dict, span, hit_score: float) -> Evidence:
    return Evidence(
        source_url=article["url"], provider=PROVIDER, title=article["title"] or "", snippet=span.quote,
        published_at=article["published_at"], discovered_at=article["discovered_at"],
        relevance_score=hit_score, page_title=article["title"], content_span=span.quote,
    )


async def extract_from_stored(session: AsyncSession, request: IntelligenceRequest) -> StoredIntelligenceResponse:
    """Query path: rank stored articles with the search, then build intelligence over exactly those articles."""
    found = await search_with_evidence(session, request.query, limit=request.limit)
    return await intelligence_from_hits(session, found.query, found.results)


async def intelligence_from_hits(session: AsyncSession, query: str, hits: list, as_of=None) -> StoredIntelligenceResponse:
    """Build the full intelligence response over an ordered list of stored-article hits (search results or a
    client's articles). Hits need: article_id, url, title, published_at, client, source, source_class, score, relevance."""
    ids = [h.article_id for h in hits]
    if not ids:
        return StoredIntelligenceResponse(query=query, provider=PROVIDER, source_result_count=0, entities=[],
                                          events=[], relationships=[], timeline=[], temporal_relationships=[],
                                          articles=[])
    text_rows = {
        r[0]: {"title": r[1], "summary": r[2], "raw_text": r[3], "fetched_at": r[4], "created_at": r[5]}
        for r in (await session.execute(
            select(Article.id, Article.title, Article.summary, Article.raw_text, Article.fetched_at, Article.created_at)
            .where(Article.id.in_(ids))
        )).all()
    }
    mentions = (await session.execute(
        select(EntityMention.article_id, EntityMention.mention_text, EntityRow.canonical_name, EntityRow.canonical_key,
               EntityRow.entity_type)
        .join(EntityRow, EntityRow.id == EntityMention.entity_id)
        .where(EntityMention.article_id.in_(ids), EntityRow.entity_type.in_(_VALID_TYPES))
    )).all()

    hit_by_id = {h.article_id: h for h in hits}
    rank = {aid: i for i, aid in enumerate(ids)}
    per_entity: dict = defaultdict(lambda: {"mentions": Counter(), "articles": set(), "evidence": [], "seen": set()})
    article_entities: dict = defaultdict(lambda: defaultdict(set))  # article -> entity key -> surface terms
    for article_id, mention_text, name, key, etype in sorted(mentions, key=lambda m: (rank[m[0]], m[3], m[1] or "")):
        row, hit = text_rows[article_id], hit_by_id[article_id]
        art = {"url": hit.url, "title": hit.title, "published_at": hit.published_at,
               "discovered_at": row["fetched_at"] or row["created_at"] or datetime.now(timezone.utc)}
        bucket = per_entity[(key, name, etype)]
        terms = sorted({t for t in (mention_text, name) if t and t.strip()})
        article_entities[article_id][key].update(terms)
        for span in find_spans(row, [t.lower() for t in terms], max_spans=1):
            if (art["url"], span.quote) not in bucket["seen"]:
                bucket["seen"].add((art["url"], span.quote))
                bucket["evidence"].append(_evidence(art, span, float((hit.relevance or {}).get("relevance_score") or hit.score)))
                bucket["articles"].add(article_id)
        if mention_text:
            bucket["mentions"][mention_text] += 1

    entities = []
    for (key, name, etype), b in per_entity.items():
        if not b["evidence"]:
            continue  # no verbatim support in stored text -> not reported
        ordered = [m for m, _ in sorted(b["mentions"].items(), key=lambda kv: (-kv[1], kv[0]))] or [name]
        entities.append(StoredEntity(
            canonical_name=name, canonical_key=key, entity_type=EntityType(etype), mention=ordered[0], mentions=ordered,
            evidence=b["evidence"], article_ids=sorted(b["articles"], key=lambda a: rank[a]), article_count=len(b["articles"]),
        ))
    entities.sort(key=lambda e: (-e.article_count, e.canonical_key))
    entity_keys = {e.canonical_key for e in entities}
    occurrences, evidence_of, when_of = [], {}, {}
    for aid in ids:
        row, hit = text_rows[aid], hit_by_id[aid]
        discovered = row["fetched_at"] or row["created_at"] or datetime.now(timezone.utc)
        art = {"url": hit.url, "title": hit.title, "published_at": hit.published_at, "discovered_at": discovered}
        for cand in extract_events(aid, row, article_entities.get(aid, {}), published_at=hit.published_at):
            occurrences.append(Occurrence(cand, aid, hit.published_at))
            evidence_of[cand.event_key] = _evidence(art, SimpleNamespace(quote=cand.quote),
                                                    float((hit.relevance or {}).get("relevance_score") or hit.score))
            when_of[cand.event_key] = (hit.published_at or discovered,
                                       "publication_fallback" if hit.published_at else "discovery_fallback")
    events, relationships, timeline_rows = [], [], []
    for group in merge_occurrences(occurrences):
        primary = group[0].candidate                       # headline-like member; its key is the event key
        evidence = [evidence_of[o.candidate.event_key] for o in group]
        article_ids_ = sorted({o.article_id for o in group}, key=lambda a: rank[a])
        dates = {o.candidate.event_date.when for o in group if o.candidate.event_date}
        stated = next((o.candidate.event_date for o in group if o.candidate.event_date), None) \
            if len(dates) == 1 else None                        # members that disagree -> keep publication fallback
        events.append(StoredEvent(
            event_key=primary.event_key, event_id=primary.event_key, entity_keys=list(primary.entity_keys), title=primary.quote, event_type=primary.event_type, evidence=evidence,
            article_ids=article_ids_, source_count=len(article_ids_),
            event_time=stated.when if stated else None,
            event_time_precision="day" if stated else "unknown",
            event_time_source="article_text" if stated else "unknown",
        ))
        earliest = min((when_of[o.candidate.event_key] for o in group), key=lambda w: (w[0], w[1]))
        if stated:
            earliest = (stated.when, "event_time")
        timeline_rows.append((primary.event_key, earliest, article_ids_))
        relationships.extend(
            EntityEventRelationship(entity_key=k, event_key=primary.event_key, relationship_type="INVOLVES",
                                    evidence=evidence)
            for k in primary.entity_keys if k in entity_keys
        )
    timeline = [
        StoredTimelineEntry(event_key=k, position=i, ordering_time=t, ordering_time_source=src, article_ids=a)
        for i, (k, (t, src), a) in enumerate(sorted(timeline_rows, key=lambda r: (r[1][0], r[0])), start=1)
    ]
    articles = [ArticleContext(article_id=h.article_id, url=h.url, title=h.title, client=h.client, source=h.source,
                               source_class=h.source_class, published_at=h.published_at, score=h.score,
                               relevance=h.relevance) for h in hits]
    base = StoredIntelligenceResponse(query=query, provider=PROVIDER, source_result_count=len(hits),
                                      entities=entities, events=events, relationships=relationships, timeline=timeline,
                                      temporal_relationships=[], articles=articles,
                                      raw_event_count=len(occurrences))
    insights = derive_insights(base, as_of)
    return base.model_copy(update={
        "insights": insights,
        "opportunities": await match_capabilities(session, derive_opportunities(base, insights, as_of)),
        "alerts": derive_alerts(base, insights, as_of),
    })
