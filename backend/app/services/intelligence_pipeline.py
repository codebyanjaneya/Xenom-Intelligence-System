"""Phase-1 intelligence pipeline: one shared core for every ingestion path.

    SOURCE -> FETCH -> PARSE -> STRUCTURAL CHECKS -> CLEAN/NORMALIZE -> DEDUPLICATE
           -> CLIENT RELEVANCE (entity + context + topic + source trust)
           -> QUALITY SCORE -> QUALITY GATE
           -> MEDIA/CANONICAL RESOLUTION (accepted only) -> CROSS-SOURCE DEDUPE -> PERSIST

Every processed article stores an explainable JSON decision in ``score_reason``:
why it was accepted, or (for rejected records) at which stage and why it failed.
Articles are only shown when the relevance stage accepted at least one client
AND the quality gate passed; a registered source alone is never enough.

Fetching is separate (adapters / ``run_intelligence_pipeline`` / registry runner);
``process_documents`` is the stage chain after fetch.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.ingestion.base import AdapterError, NormalizedDocument
from app.adapters.ingestion.google_news import GoogleNewsAdapter
from app.adapters.ingestion.rss import RssHttpAdapter
from app.adapters.ingestion.site_adapters import LinkListingAdapter, robots_allowed
from app.config.intelligence_themes import TECH_THEMES
from app.models import Article, Source
from app.repositories import articles as article_repo
from app.services.article_page import FETCH_HEADERS, fetch_article_page, is_acceptable_image_url, pick_valid_image
from app.services.client_relevance import MIN_ENTITY_EVIDENCE, RelevanceDecision, evaluate_all_clients
from app.services.entity_matcher import attach_mention
from app.services.google_news_decoder import is_google_news_url, resolve_google_news_url
from app.services.intelligence_scoring import QUALITY_GATE_DEFAULT, score_article
from app.services.normalization import normalize_document
from app.services.chunking import chunk_article
from app.services.profiles import refresh_configs
from app.services.researchers import record_authors
from app.services.taxonomy import label_article, load_profiles, research_topic_terms
from app.services.quality_checks import validate_record
from app.services.topic_tagger import tag_article_topics

logger = logging.getLogger(__name__)

__all__ = ["IntelligencePipelineResult", "PipelineContext", "process_documents", "run_intelligence_pipeline"]


@dataclass
class PipelineContext:
    """Per-run settings supplied by whoever owns the source (registry, scheduler, API)."""

    client_key: str | None = None        # client this source was registered for (reporting only)
    source_kind: str | None = None       # official | directory | reference | research | None (news)
    require_date: bool = True            # reject records that have no publication date
    resolve_media: bool = False          # resolve Google wrappers + validate/attach images
    max_media: int = 150                 # cap on network enrichment per run
    label_profiles: dict | None = None   # effective client profiles, loaded once per run for taxonomy labelling
    research_gate_offset: int = 15       # research-class documents have no client entity; the gate is threshold - offset
    sample_limit: int = 6                # diagnostics samples kept per decision type


class IntelligencePipelineResult:
    """Per-stage counters plus diagnostics for one source run."""

    def __init__(self) -> None:
        self.documents_fetched = 0
        self.documents_parsed = 0
        self.documents_invalid = 0            # failed structural checks (not persisted)
        self.documents_cleaned = 0
        self.documents_deduplicated = 0       # already known (same source or same URL anywhere)
        self.documents_entity_candidates = 0  # had entity evidence for at least one client
        self.documents_tagged_entity = 0      # relevance accepted for >=1 client
        self.documents_tagged_topic = 0
        self.documents_scored = 0
        self.documents_quality_passed = 0
        self.documents_cross_deduplicated = 0
        self.documents_accepted = 0           # persisted and visible
        self.documents_rejected = 0           # persisted with status=rejected (diagnostics)
        self.rows_created = 0
        self.canonical_resolved = 0
        self.canonical_failed = 0
        self.images_found = 0
        self.images_missing = 0
        self.media_skipped_limit = 0
        self.rejection_reasons: Counter[str] = Counter()
        self.errors: list[str] = []
        self.samples: dict[str, list[dict]] = {"accepted": [], "rejected": [], "invalid": []}
        self.per_client_accepted: Counter[str] = Counter()

    def summary(self) -> str:
        return (
            f"Fetched: {self.documents_fetched} | Parsed: {self.documents_parsed} | Invalid: {self.documents_invalid} | "
            f"Cleaned: {self.documents_cleaned} | Deduplicated: {self.documents_deduplicated} | "
            f"Entity candidates: {self.documents_entity_candidates} | Relevant: {self.documents_tagged_entity} | "
            f"Quality passed: {self.documents_quality_passed} | Cross-dedup: {self.documents_cross_deduplicated} | "
            f"Accepted: {self.documents_accepted} | Rejected: {self.documents_rejected}"
        )

    def as_dict(self) -> dict:
        data = {k: v for k, v in vars(self).items() if isinstance(v, (int, str))}
        data["rejection_reasons"] = dict(self.rejection_reasons)
        data["per_client_accepted"] = dict(self.per_client_accepted)
        data["errors"] = list(self.errors)
        data["samples"] = self.samples
        return data

    def _sample(self, kind: str, limit: int, item: dict) -> None:
        if len(self.samples[kind]) < limit:
            self.samples[kind].append(item)


def _host(url: str) -> str:
    return urlparse(url).hostname or ""


def _technology_terms(title: str | None, body: str | None) -> list[str]:
    text = f"{title or ''} {body or ''}"
    return [theme.key for theme in TECH_THEMES if re.search(theme.pattern, text, re.IGNORECASE)]


_REJECTION_PRIORITY = {
    "HARD_NEGATIVE": 0,
    "BELOW_RELEVANCE_THRESHOLD": 1,
    "WEAK_ACRONYM_NO_CONTEXT": 2,
    "NO_ENTITY_EVIDENCE": 3,
}


def _rank_rejections(decisions: dict[str, RelevanceDecision]) -> list[RelevanceDecision]:
    """Most informative rejection first (a JCB 'for sale' hard negative beats another client's 'no evidence')."""
    return sorted(
        decisions.values(),
        key=lambda d: (_REJECTION_PRIORITY.get(d.rejection_reason or "", 9), -d.relevance_score),
    )


def _evidence(
    *,
    decision: str,
    stage: str,
    reason: str | None,
    decisions: dict[str, RelevanceDecision],
    accepted: list[RelevanceDecision],
    quality: dict | None = None,
    extra: dict | None = None,
) -> str:
    ranked = _rank_rejections(decisions)
    best = accepted[0] if accepted else (ranked[0] if ranked else None)
    payload: dict = {
        "decision": decision,
        "stage": stage,
        "reason": reason,
        "entity": best.entity_key if best else None,
        "matched_terms": best.matched_terms if best else [],
        "matched_topics": best.matched_topics if best else [],
        "source_type": best.source_type if best else None,
        "source_domain": best.source_domain if best else None,
        "relevance_score": round(best.relevance_score, 3) if best else 0.0,
        "entities": [d.as_dict() for d in accepted],
    }
    if not accepted:
        payload["rejected_candidates"] = [
            {"entity": d.entity_key, "reason": d.rejection_reason, "relevance_score": round(d.relevance_score, 3),
             "negatives_hit": d.negatives_hit}
            for d in ranked[:2]
        ]
    if quality:
        payload["quality"] = quality
    if extra:
        payload.update(extra)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


async def _enrich_media(
    article: Article, doc: NormalizedDocument, http: httpx.AsyncClient, result: IntelligencePipelineResult
) -> dict:
    """Resolve the Google wrapper to the publisher URL and attach a validated article image."""
    info: dict = {}
    meta = doc.metadata or {}
    candidates: list[tuple[str, str]] = []
    legacy = meta.get("image_url")
    if legacy:
        candidates.append(("rss_media", legacy))
    candidates += [(c[0], c[1]) for c in meta.get("image_candidates", []) if len(c) == 2]

    target_url = article.url
    if is_google_news_url(article.url):
        await asyncio.sleep(0.4)  # be polite to Google
        real = await resolve_google_news_url(article.url, http)
        if real:
            result.canonical_resolved += 1
            target_url = real
            article.canonical_url = real
            info["publisher_url"] = real
        else:
            result.canonical_failed += 1
            info["publisher_url"] = None
            target_url = ""
    else:
        article.canonical_url = article.canonical_url or article.url

    fetch_page = bool(target_url) and meta.get("adapter") != "table_rows" and not target_url.lower().endswith(".pdf")
    if fetch_page and (is_google_news_url(article.url) or not candidates or article.canonical_url is None):
        if await robots_allowed(target_url, http):
            page = await fetch_article_page(target_url, client=http)
            if page.error:
                info["page_error"] = page.error
            else:
                if page.canonical_url and not is_google_news_url(page.canonical_url):
                    article.canonical_url = page.canonical_url
                if article.published_at is None and page.published_at:
                    article.published_at = page.published_at
                if page.author and not article.author:
                    article.author = page.author
                page_text = (page.body_text or page.description or "")[:1500]
                if len(page_text) > len(article.summary or ""):
                    article.summary = page_text
                candidates += page.image_candidates
        else:
            info["page_error"] = "robots-disallowed"

    image, source, rejected = await pick_valid_image(candidates, http)
    if image:
        article.image_url = image
        result.images_found += 1
        info["image_source"] = source
    else:
        article.image_url = None
        result.images_missing += 1
        if rejected:
            info["image_rejections"] = rejected[:4]
    return info


async def process_documents(
    session: AsyncSession,
    source: Source,
    documents: list[NormalizedDocument],
    *,
    quality_gate_threshold: int = QUALITY_GATE_DEFAULT,
    ctx: PipelineContext | None = None,
    result: IntelligencePipelineResult | None = None,
    persist: bool = True,
) -> IntelligencePipelineResult:
    """``persist=False`` is the source dry run: the identical stage chain runs, then the caller rolls the session back."""
    ctx = ctx or PipelineContext()
    result = result or IntelligencePipelineResult()
    result.documents_fetched = result.documents_fetched or len(documents)
    media_used = 0
    if documents:
        await refresh_configs(session)  # saved client profiles (if any) become the rule configuration for this run
        ctx.label_profiles = await load_profiles(session)

    async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers=FETCH_HEADERS) as http:
        for doc in documents:
            try:
                async with session.begin_nested():
                    media_used = await _process_one(session, source, doc, ctx, quality_gate_threshold, result, http, media_used)
            except Exception as exc:  # isolated per document via SAVEPOINT; reported, never silent
                logger.error("Error processing document %r: %s", (doc.title or "")[:60], exc, exc_info=True)
                result.errors.append(f"{(doc.title or '')[:50]}: {type(exc).__name__}: {str(exc)[:120]}")

    try:
        if persist:
            await session.commit()
        else:
            await session.flush()
    except Exception as exc:
        logger.error("Commit failed: %s", exc, exc_info=True)
        await session.rollback()
        result.errors.append(f"Commit failed: {exc}")
    logger.info("Pipeline complete for %s: %s", source.name, result.summary())
    return result


async def _process_one(
    session: AsyncSession,
    source: Source,
    doc: NormalizedDocument,
    ctx: PipelineContext,
    threshold: int,
    result: IntelligencePipelineResult,
    http: httpx.AsyncClient,
    media_used: int,
) -> int:
    # PARSE ------------------------------------------------------------------
    result.documents_parsed += 1

    # STRUCTURAL CHECKS ------------------------------------------------------
    problems = validate_record(
        title=doc.title, url=doc.canonical_url, published_at=doc.published_at,
        text=doc.normalized_content, require_date=ctx.require_date,
    )
    if problems:
        result.documents_invalid += 1
        result.rejection_reasons[f"STRUCTURAL:{problems[0]}"] += 1
        result._sample("invalid", ctx.sample_limit, {"title": (doc.title or "")[:90], "problems": problems})
        return media_used

    # CLEAN / NORMALIZE ------------------------------------------------------
    normalized = normalize_document(doc)
    result.documents_cleaned += 1

    # DEDUPLICATE (in-source semantics unchanged + global URL check) -----------
    existing = await article_repo.find_duplicate(
        session, source_id=normalized.source_id, normalized_url=normalized.normalized_url,
        content_hash=normalized.content_hash,
    ) or await article_repo.find_by_url_anywhere(
        session, url=normalized.original_url, normalized_url=normalized.normalized_url
    )
    if existing is not None:
        result.documents_deduplicated += 1
        return media_used

    # CLIENT RELEVANCE -------------------------------------------------------
    meta = doc.metadata or {}
    decisions = evaluate_all_clients(
        title=normalized.title,
        body=normalized.normalized_content,
        source_domain=meta.get("publisher_domain") or _host(normalized.original_url),
        source_kind=ctx.source_kind,
        source_client=ctx.client_key,
    )
    accepted = sorted((d for d in decisions.values() if d.accepted), key=lambda d: d.relevance_score, reverse=True)
    if any(d.entity_points >= MIN_ENTITY_EVIDENCE for d in decisions.values()):
        result.documents_entity_candidates += 1

    is_wrapper = is_google_news_url(normalized.original_url)
    article = Article(
        source_id=normalized.source_id,
        url=normalized.original_url,
        canonical_url=None if is_wrapper else normalized.original_url,
        normalized_url=normalized.normalized_url,
        content_hash=normalized.content_hash,
        title=normalized.title,
        author=meta.get("author"),
        published_at=normalized.published_at,
        fetched_at=normalized.fetched_at,
        raw_text=normalized.raw_content,
        summary=(normalized.normalized_content or "")[:1500],
        processing_status="parsed",
        source_metadata={k: v for k, v in (meta.get("source_metadata") or {}).items() if v is not None} or None,
    )
    session.add(article)
    await session.flush()  # article.id for cross-source checks and mentions

    def reject(stage: str, reason: str, quality: dict | None = None, extra: dict | None = None) -> None:
        article.processing_status = "rejected"
        article.quality_gate_passed = False
        article.score_reason = _evidence(
            decision="rejected", stage=stage, reason=reason, decisions=decisions, accepted=accepted,
            quality=quality, extra=extra,
        )
        result.documents_rejected += 1
        result.rows_created += 1
        result.rejection_reasons[f"{stage.upper()}:{reason}"] += 1
        top = _rank_rejections(decisions)[0]
        result._sample("rejected", ctx.sample_limit, {
            "title": (article.title or "")[:90], "stage": stage, "reason": reason,
            "best_client": top.entity_key, "relevance": round(top.relevance_score, 2),
        })

    research_terms: list[str] = []
    if not accepted and ctx.source_kind == "research":
        research_terms = research_topic_terms(article.title, article.summary, ctx.label_profiles)
    if research_terms and source.tier != "TIER_4":
        return await _accept_research(session, article, doc, source, ctx, threshold, result, research_terms, reject, media_used)
    technology_terms = _technology_terms(article.title, article.summary) if not accepted and ctx.source_kind == "technology" else []
    if technology_terms and source.tier != "TIER_4":
        return await _accept_unattributed(session, article, doc, source, ctx, threshold, result, technology_terms, reject, media_used)
    if not accepted:
        article.relevance_score = 0.0
        reject("relevance", _rank_rejections(decisions)[0].rejection_reason or "NO_ENTITY_EVIDENCE")
        return media_used

    if source.tier == "TIER_4":
        # PDF s.6: tier 4 (forums, user-generated content) is a discovery signal only: kept with its reason, never evidence.
        article.relevance_score = 0.0
        reject("source_tier", "DISCOVERY_SIGNAL_ONLY")
        return media_used

    result.documents_tagged_entity += 1

    # TOPIC TAGGING + QUALITY SCORE + GATE -----------------------------------
    topics = tag_article_topics(article.title, article.summary)
    if topics:
        article.topic = "|".join(t.value for t in topics)
        result.documents_tagged_topic += 1

    # MEDIA / CANONICAL RESOLUTION (relevance-accepted only) ---------------------------
    # Runs before scoring so 'context' is judged on the real article text, not the feed's title stub.
    extra: dict = {}
    if ctx.resolve_media:
        if media_used < ctx.max_media:
            extra = await _enrich_media(article, doc, http, result)
            media_used += 1
        else:
            result.media_skipped_limit += 1
    elif meta.get("image_url") and is_acceptable_image_url(meta["image_url"]):
        article.image_url = meta["image_url"]

    # QUALITY SCORE + GATE (threshold unchanged) --------------------------------------
    # technology/industry inputs are the client's OWN vocabulary hits from the relevance stage
    # (what the scoring function's parameters were designed for), not a generic topic-enum guess.
    breakdown = score_article(
        title=article.title,
        summary=article.summary,
        published_at=article.published_at,
        source_tier=source.tier,
        entity_mention_count=len(accepted),
        has_product_match=any(d.product_hit for d in accepted),
        technology_keywords_found=min(3, max(d.context_hits for d in accepted)),
        industry_keywords_found=max(d.industry_hits for d in accepted),
    )
    result.documents_scored += 1
    article.relevance_score = breakdown.total_score / 100.0
    quality = {"score": round(breakdown.total_score / 100.0, 3), "threshold": threshold / 100.0,
               "reasons": breakdown.reasons[:5]}
    if breakdown.total_score < threshold:
        reject("quality_gate", "BELOW_QUALITY_THRESHOLD", quality, extra)
        return media_used
    result.documents_quality_passed += 1

    # CROSS-SOURCE DEDUPE ------------------------------------------------------
    duplicate = await article_repo.find_cross_source_duplicate(
        session,
        article_id=article.id,
        urls=[article.canonical_url] if article.canonical_url and article.canonical_url != article.url else [],
        title=article.title,
        published_at=article.published_at,
    )
    if duplicate is not None:
        result.documents_cross_deduplicated += 1
        reject("duplicate", "DUPLICATE_OF_ACCEPTED", quality, {"duplicate_of": str(duplicate.id), **extra})
        return media_used

    # PERSIST ---------------------------------------------------------------
    article.processing_status = "accepted"
    article.quality_gate_passed = True
    article.score_reason = _evidence(
        decision="accepted", stage="accepted", reason=None, decisions=decisions, accepted=accepted,
        quality=quality, extra={"quality_score": quality["score"], **extra},
    )
    for d in accepted:
        await attach_mention(session, article, d.entity_key, d.matched_terms[0] if d.matched_terms else None)
        result.per_client_accepted[d.entity_key] += 1
    result.documents_accepted += 1
    result.rows_created += 1
    await _index_article(session, article, doc, ctx, result)
    best = accepted[0]
    result._sample("accepted", ctx.sample_limit, {
        "title": (article.title or "")[:90], "client": best.entity_key, "relevance": round(best.relevance_score, 2),
        "quality": quality["score"], "terms": best.matched_terms, "topics": best.matched_topics[:3],
        "image": bool(article.image_url), "domain": best.source_domain,
    })
    return media_used


async def _accept_research(session, article, doc, source, ctx, threshold, result, research_terms, reject, media_used) -> int:
    """Research-class documents (papers, model hubs) are admitted on a stated research/technology topic even when no client
    rule accepts them (PDF s.4, s.11). No client is asserted: the row carries no client mention, so client views never show it."""
    breakdown = score_article(title=article.title, summary=article.summary, published_at=article.published_at, source_tier=source.tier,
                              entity_mention_count=0, has_product_match=False, technology_keywords_found=min(3, len(research_terms)),
                              industry_keywords_found=0)
    result.documents_scored += 1
    gate = max(0, threshold - ctx.research_gate_offset)
    article.relevance_score = breakdown.total_score / 100.0
    quality = {"score": round(breakdown.total_score / 100.0, 3), "threshold": gate / 100.0, "reasons": breakdown.reasons[:5], "research_gate": True}
    if breakdown.total_score < gate:
        article.processing_status, article.quality_gate_passed = "rejected", False
        article.score_reason = json.dumps({"decision": "rejected", "stage": "quality_gate", "reason": "BELOW_QUALITY_THRESHOLD", "entity": None,
                                           "matched_terms": research_terms[:8], "source_type": "research", "quality": quality}, ensure_ascii=False, separators=(",", ":"))
        result.documents_rejected += 1
        result.rows_created += 1
        result.rejection_reasons["QUALITY_GATE:BELOW_QUALITY_THRESHOLD"] += 1
        return media_used
    result.documents_quality_passed += 1
    duplicate = await article_repo.find_cross_source_duplicate(session, article_id=article.id, urls=[], title=article.title, published_at=article.published_at)
    if duplicate is not None:
        result.documents_cross_deduplicated += 1
        reject("duplicate", "DUPLICATE_OF_ACCEPTED", quality, {"duplicate_of": str(duplicate.id)})
        return media_used
    article.processing_status, article.quality_gate_passed = "accepted", True
    article.canonical_url = article.canonical_url or article.url
    article.score_reason = json.dumps({"decision": "accepted", "stage": "accepted", "reason": None, "entity": None, "matched_terms": research_terms[:8],
                                       "matched_topics": ["research"], "source_type": "research", "source_domain": _host(article.url),
                                       "relevance_score": round(breakdown.total_score / 100.0, 3), "entities": [], "research_topic_match": research_terms[:8],
                                       "quality": quality, "quality_score": quality["score"]}, ensure_ascii=False, separators=(",", ":"))
    result.documents_accepted += 1
    result.rows_created += 1
    result._sample("accepted", ctx.sample_limit, {"title": (article.title or "")[:90], "client": None, "relevance": quality["score"], "quality": quality["score"],
                                                  "terms": research_terms[:4], "topics": ["research"], "image": False, "domain": _host(article.url)})
    await _index_article(session, article, doc, ctx, result)
    return media_used


async def _accept_unattributed(session, article, doc, source, ctx, threshold, result, terms, reject, media_used) -> int:
    """Accept a literal technology match from a cross-client source without inventing a client attribution."""
    breakdown = score_article(title=article.title, summary=article.summary, published_at=article.published_at,
                              source_tier=source.tier, entity_mention_count=0, has_product_match=False,
                              technology_keywords_found=min(3, len(terms)), industry_keywords_found=0)
    result.documents_scored += 1
    article.relevance_score = breakdown.total_score / 100.0
    technology_threshold = max(20, threshold - 20)
    quality = {"score": round(breakdown.total_score / 100.0, 3), "threshold": technology_threshold / 100.0,
               "reasons": breakdown.reasons[:5], "unattributed_source_gate": "technology"}
    if breakdown.total_score < technology_threshold:
        article.processing_status, article.quality_gate_passed = "rejected", False
        article.score_reason = json.dumps({"decision": "rejected", "stage": "quality_gate", "reason": "BELOW_QUALITY_THRESHOLD",
                                           "entity": None, "matched_terms": terms[:8], "source_type": "technology",
                                           "quality": quality}, ensure_ascii=False, separators=(",", ":"))
        result.documents_rejected += 1
        result.rows_created += 1
        result.rejection_reasons["QUALITY_GATE:BELOW_QUALITY_THRESHOLD"] += 1
        return media_used
    result.documents_quality_passed += 1
    duplicate = await article_repo.find_cross_source_duplicate(session, article_id=article.id, urls=[], title=article.title,
                                                               published_at=article.published_at)
    if duplicate is not None:
        result.documents_cross_deduplicated += 1
        reject("duplicate", "DUPLICATE_OF_ACCEPTED", quality, {"duplicate_of": str(duplicate.id)})
        return media_used
    article.processing_status, article.quality_gate_passed = "accepted", True
    article.canonical_url = article.canonical_url or article.url
    article.score_reason = json.dumps({"decision": "accepted", "stage": "accepted", "reason": None, "entity": None,
                                       "matched_terms": terms[:8], "matched_topics": terms[:8], "source_type": "technology",
                                       "source_domain": _host(article.url), "relevance_score": round(article.relevance_score, 3),
                                       "entities": [], "quality": quality, "quality_score": quality["score"]},
                                      ensure_ascii=False, separators=(",", ":"))
    result.documents_accepted += 1
    result.rows_created += 1
    await _index_article(session, article, doc, ctx, result)
    result._sample("accepted", ctx.sample_limit, {"title": (article.title or "")[:90], "client": None,
                                                   "relevance": quality["score"], "quality": quality["score"],
                                                   "terms": terms[:4], "topics": terms[:4], "image": False,
                                                   "domain": _host(article.url)})
    return media_used


async def _index_article(session: AsyncSession, article: Article, doc: NormalizedDocument, ctx: PipelineContext, result: IntelligencePipelineResult) -> None:
    """Evidence store + taxonomy for an accepted article: chunks with offsets and rule labels. A failure here is reported on
    the run and never drops the article. Embeddings are written later by ``embed_pending`` when a provider is configured."""
    try:
        async with session.begin_nested():
            await chunk_article(session, article)
            await label_article(session, article, profiles=ctx.label_profiles)
            await record_authors(session, article, (doc.metadata or {}).get("authors"))
    except Exception as exc:  # noqa: BLE001 - isolated, reported
        logger.error("Indexing failed for %s: %s", article.id, exc, exc_info=True)
        result.errors.append(f"index {(article.title or '')[:40]}: {type(exc).__name__}: {str(exc)[:100]}")


async def run_intelligence_pipeline(
    session: AsyncSession,
    source: Source,
    query: str | None = None,
    quality_gate_threshold: int = QUALITY_GATE_DEFAULT,
    ctx: PipelineContext | None = None,
) -> IntelligencePipelineResult:
    """Fetch for one source (HTML listing, Google News query, or RSS feed) and run the stage chain."""
    result = IntelligencePipelineResult()
    ctx = ctx or PipelineContext()
    retrieved_at = datetime.now(timezone.utc)
    documents: list[NormalizedDocument] = []

    try:
        if source.source_type == "HTML":
            adapter = LinkListingAdapter()
            documents = await adapter.fetch(source.canonical_url, source_id=source.id, retrieved_at=retrieved_at)
            result.errors.extend(adapter.errors)
            ctx.require_date = False
        elif source.source_type == "RSS" and query:
            documents = await GoogleNewsAdapter().fetch(query=query, source_id=str(source.id), retrieved_at=retrieved_at)
        elif source.source_type == "RSS" and source.feed_url:
            documents = await RssHttpAdapter().fetch(source.feed_url, source_id=source.id, retrieved_at=retrieved_at)
        else:
            result.errors.append(f"No fetch strategy for source {source.name} (type={source.source_type})")
            return result
    except AdapterError as exc:
        logger.error("Adapter error for %s: %s", source.name, exc)
        result.errors.append(f"Adapter error: {exc}")
        return result

    result.documents_fetched = len(documents)
    if not documents:
        logger.warning("No documents fetched for %s", source.name)
        return result
    return await process_documents(
        session, source, documents, quality_gate_threshold=quality_gate_threshold, ctx=ctx, result=result
    )
