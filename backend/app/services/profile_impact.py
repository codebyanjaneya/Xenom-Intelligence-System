"""Impact preview, background re-evaluation and suggest-then-confirm for client profiles (PDF s.7, s.12).

* preview_impact  - how many ALREADY-STORED articles (accepted and rejected) a proposed profile would add or remove
                    for the client. Read-only; the live rule configuration is not touched.
* reevaluate      - re-run the relevance stage over every stored article for one client under the active profile,
                    including rejected ones, and persist the changed decisions with their reason.
* suggestions     - deterministic keyword/query suggestions mined from the client's own accepted articles, each with the
                    reason and example matches. A suggestion changes nothing until a manager accepts it.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from urllib.parse import urlparse

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.clients import ENTITY_KEY_BY_CLIENT
from app.config.entity_config import EntityConfig
from app.models.architecture import ProfileSuggestion
from app.models.content import Article
from app.models.entities import Entity, EntityMention
from app.models.sources import Source
from app.repositories import articles as article_repo
from app.services import profiles as prof
from app.services.client_relevance import evaluate_relevance
from app.services.entity_matcher import attach_mention
from app.services.intelligence_scoring import score_article
from app.services.markup import strip_markup

PREVIEW_LIMIT = 5000
SAMPLE = 8


def _decision(entity_key: str, article: Article, source_kind: str | None, cfg: EntityConfig):
    host = urlparse(article.canonical_url or article.url).hostname or ""
    return evaluate_relevance(
        entity_key, title=article.title, body=article.raw_text or article.summary, source_domain=host,
        source_kind=source_kind, config=cfg,
    )


async def _articles(session: AsyncSession) -> list[tuple[Article, str | None]]:
    rows = (await session.execute(
        select(Article, Source.source_class).outerjoin(Source, Article.source_id == Source.id)
        .where(Article.processing_status.in_(("accepted", "rejected"))).order_by(Article.id).limit(PREVIEW_LIMIT)
    )).all()
    return [(a, c if c in {"official", "directory", "reference", "research"} else None) for a, c in rows]


async def preview_impact(session: AsyncSession, client: str, proposed: prof.ProfileData) -> dict:
    _, name = prof.resolve(client)
    entity_key = ENTITY_KEY_BY_CLIENT[name]
    current_cfg = prof.entity_config_for(name, (await prof.effective_profile(session, name))[1])
    proposed_cfg = prof.entity_config_for(name, proposed)
    added, removed, unchanged = [], [], 0
    total = 0
    for article, kind in await _articles(session):
        total += 1
        now, then = _decision(entity_key, article, kind, current_cfg), _decision(entity_key, article, kind, proposed_cfg)
        if now.accepted == then.accepted:
            unchanged += 1
            continue
        entry = {"article_id": str(article.id), "title": strip_markup(article.title), "stored_status": article.processing_status,
                 "current": {"accepted": now.accepted, "score": round(now.relevance_score, 3), "reason": now.rejection_reason},
                 "proposed": {"accepted": then.accepted, "score": round(then.relevance_score, 3), "reason": then.rejection_reason}}
        (added if then.accepted else removed).append(entry)
    return {"client": name, "evaluated_articles": total, "would_add": len(added), "would_remove": len(removed),
            "unchanged": unchanged, "added_examples": added[:SAMPLE], "removed_examples": removed[:SAMPLE],
            "note": "Relevance stage only; a newly relevant article still has to pass the quality gate when re-evaluated."}


async def _previous_profile(session: AsyncSession, name: str) -> prof.ProfileData:
    """The profile the ACTIVE version replaced (or the code baseline when the active one is the first)."""
    versions = await prof.list_versions(session, name)  # newest first
    active = next((v for v in versions if v.is_active), None)
    older = [v for v in versions if active and v.version < active.version]
    return prof.ProfileData.model_validate(older[0].data) if older else prof.code_profile(name)


async def reevaluate(session: AsyncSession, client: str, *, gate: int = 40, actor: str | None = None,
                     baseline: prof.ProfileData | None = None) -> dict:
    """Apply the ACTIVE profile to stored articles (accepted AND rejected) for this client. Only articles whose relevance
    decision differs between the previous profile and the active one change, so a re-run without a profile change is a
    no-op and decisions made under older rules are never re-litigated. Returns counts of what changed."""
    _, name = prof.resolve(client)
    entity_key = ENTITY_KEY_BY_CLIENT[name]
    await prof.refresh_configs(session)
    cfg = prof.ENTITY_CONFIGS[entity_key]
    old_cfg = prof.entity_config_for(name, baseline or await _previous_profile(session, name))
    entity = await session.scalar(select(Entity).where(Entity.canonical_key == name.lower(), Entity.entity_type == "ORG"))
    counts = Counter()
    for article, kind in await _articles(session):
        counts["evaluated"] += 1
        decision = _decision(entity_key, article, kind, cfg)
        if _decision(entity_key, article, kind, old_cfg).accepted == decision.accepted:
            counts["unchanged"] += 1
            continue
        mentioned = entity is not None and await session.scalar(
            select(EntityMention.id).where(EntityMention.article_id == article.id, EntityMention.entity_id == entity.id)) is not None
        if decision.accepted and not mentioned and article.processing_status == "rejected":
            evidence = _load(article.score_reason)
            if evidence.get("stage") in {"duplicate", "structural"}:
                continue  # rejected for reasons a profile cannot change
            breakdown = score_article(
                title=article.title, summary=article.summary, published_at=article.published_at,
                source_tier=(await session.get(Source, article.source_id)).tier if article.source_id else "TIER_3",
                entity_mention_count=1, has_product_match=decision.product_hit,
                technology_keywords_found=min(3, decision.context_hits), industry_keywords_found=decision.industry_hits,
            )
            if breakdown.total_score < gate:
                counts["relevant_but_below_quality_gate"] += 1
                continue
            duplicate = await article_repo.find_cross_source_duplicate(
                session, article_id=article.id, urls=[article.canonical_url] if article.canonical_url and article.canonical_url != article.url else [],
                title=article.title, published_at=article.published_at)
            if duplicate is not None:
                counts["duplicate_of_accepted"] += 1
                continue
            article.processing_status, article.quality_gate_passed = "accepted", True
            article.relevance_score = breakdown.total_score / 100.0
            article.score_reason = json.dumps({**evidence, "decision": "accepted", "stage": "accepted", "reason": None,
                                               "entity": entity_key, "matched_terms": decision.matched_terms,
                                               "reevaluated_by_profile": True, "entities": [decision.as_dict()]},
                                              ensure_ascii=False, separators=(",", ":"))
            await attach_mention(session, article, entity_key, decision.matched_terms[0] if decision.matched_terms else None)
            counts["newly_accepted"] += 1
        elif not decision.accepted and mentioned and article.processing_status == "accepted":
            await session.execute(delete(EntityMention).where(EntityMention.article_id == article.id, EntityMention.entity_id == entity.id))
            remaining = await session.scalar(select(EntityMention.id).where(EntityMention.article_id == article.id))
            if remaining is None:
                evidence = _load(article.score_reason)
                article.processing_status, article.quality_gate_passed = "rejected", False
                article.score_reason = json.dumps({**evidence, "decision": "rejected", "stage": "relevance",
                                                   "reason": decision.rejection_reason or "PROFILE_CHANGE",
                                                   "reevaluated_by_profile": True}, ensure_ascii=False, separators=(",", ":"))
                counts["newly_rejected"] += 1
            else:
                counts["client_link_removed_other_clients_kept"] += 1
    await prof.audit(session, actor=actor, action="profile.reevaluate", object_type="client_profile", object_key=name.lower(), after=dict(counts))
    await session.commit()
    return dict(counts)


def _load(raw: str | None) -> dict:
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


# ---------------------------------------------------------------------------------------- suggest, then confirm
_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9&\-]{2,}")
_STOP = frozenset(
    "the and for with from that this are was were has have had its into over about after new says said will more than "
    "also their which what when where while india indian million billion crore year years week days today report news".split()
)


def _phrases(text: str) -> set[str]:
    words = [w for w in _TOKEN.findall(text)]
    out: set[str] = set()
    for i, w in enumerate(words):
        lw = w.lower()
        if lw not in _STOP and len(lw) > 3:
            out.add(lw)
        if i + 1 < len(words):
            a, b = words[i].lower(), words[i + 1].lower()
            if a not in _STOP and b not in _STOP and len(a) > 2 and len(b) > 2:
                out.add(f"{a} {b}")
    return out


async def generate_suggestions(session: AsyncSession, client: str, *, min_articles: int = 3, limit: int = 15) -> list[ProfileSuggestion]:
    """Keyword suggestions from the client's OWN accepted articles: phrases in >= min_articles articles from >= 2
    publishers that the current profile does not already contain. Stored as 'pending' with reason and examples."""
    _, name = prof.resolve(client)
    _, data, _o = await prof.effective_profile(session, name)
    known = {t.casefold() for field in (data.strong_keywords, data.aliases, data.context_keywords, data.products, data.industries,
                                        data.technologies, data.competitors, data.executives, data.legal_names) for t in field}
    known |= {w for t in known for w in t.split()}
    rows = (await session.execute(
        select(Article.id, Article.title, Article.summary, Source.name, Article.url)
        .outerjoin(Source, Source.id == Article.source_id)
        .join(EntityMention, EntityMention.article_id == Article.id).join(Entity, Entity.id == EntityMention.entity_id)
        .where(Entity.canonical_key == name.lower(), Entity.entity_type == "ORG", Article.processing_status == "accepted")
        .order_by(Article.id).limit(1000))).all()
    docs: dict[str, set[str]] = defaultdict(set)
    hosts: dict[str, set[str]] = defaultdict(set)
    titles: dict[str, str] = {}
    for aid, title, summary, publisher, url in rows:
        titles[str(aid)] = strip_markup(title) or ""
        host = publisher or urlparse(url).hostname or ""
        for phrase in _phrases(f"{title or ''} {summary or ''}"):
            docs[phrase].add(str(aid))
            hosts[phrase].add(host)
    ranked = sorted(((p, ids) for p, ids in docs.items() if len(ids) >= min_articles and len(hosts[p]) >= 2 and p not in known
                     and not all(w in known for w in p.split())),
                    key=lambda kv: (-len(kv[1]), -len(kv[0].split()), kv[0]))
    existing = {(s.kind, s.value) for s in (await session.scalars(select(ProfileSuggestion).where(ProfileSuggestion.client_key == name.lower()))).all()}
    created: list[ProfileSuggestion] = []
    for phrase, ids in ranked:
        if len(created) >= limit:
            break
        if ("keyword", phrase) in existing:
            continue
        examples = sorted(ids)[:3]
        row = ProfileSuggestion(
            client_key=name.lower(), kind="keyword", value=phrase, method="corpus_terms", example_article_ids=examples,
            reason=f"'{phrase}' appears in {len(ids)} of {len(rows)} accepted articles for {name} from {len(hosts[phrase])} publishers "
                   f"and is not in the profile. Examples: " + "; ".join(titles[i][:80] for i in examples))
        session.add(row)
        created.append(row)
    await session.commit()
    return created


async def decide_suggestion(session: AsyncSession, suggestion_id, *, accept: bool, actor: str | None) -> tuple[ProfileSuggestion, int | None]:
    row = await session.get(ProfileSuggestion, suggestion_id)
    if row is None:
        raise LookupError("suggestion not found")
    if row.status != "pending":
        raise ValueError(f"suggestion already {row.status}")
    version = None
    if accept:
        _, name = prof.resolve(row.client_key)
        _v, data, _o = await prof.effective_profile(session, name)
        target = {"keyword": "context_keywords", "query": "google_queries", "exclusion": "negative_keywords", "competitor": "competitors",
                  "technology": "technologies"}[row.kind]
        merged = data.model_copy(update={target: [*getattr(data, target), row.value]})
        saved = await prof.save_profile(session, name, prof.ProfileData.model_validate(merged.model_dump()), actor=actor,
                                        note=f"accepted suggestion: {row.kind} '{row.value}'")
        version = saved.version
    row.status, row.decided_by = ("accepted" if accept else "dismissed"), actor
    await prof.audit(session, actor=actor, action="suggestion.accept" if accept else "suggestion.dismiss", object_type="profile_suggestion",
                     object_key=f"{row.client_key}:{row.kind}:{row.value}")
    await session.commit()
    return row, version
