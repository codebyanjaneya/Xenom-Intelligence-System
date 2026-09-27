"""Researcher and AI-ecosystem intelligence (PDF s.11): people, organisations, research areas and recent work, built on
PERSON / ORG entities, WORKS_AT relationships with evidence, and ``document_authors``.

Only what a stored document states is recorded: authorship comes from the source's own author metadata, an affiliation only when
the source gives one, and research areas are the literal technology / model terms of the person's stored documents. The
supplied researcher list is a seed (a PERSON entity without documents); ranking always comes from stored evidence, so a seed
with no documents is listed last and says so.

Similar researchers: nearest neighbours over the embeddings of each person's recent work when a provider is configured and the
chunks are embedded; otherwise the overlap of their literal area/title terms (the response states the method).
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.clients import CLIENT_CONFIG
from app.models.architecture import ArticleLabel, DocumentAuthor
from app.models.content import Article
from app.models.entities import Entity, EntityAlias, EntityMention, Relationship, RelationshipEvidence
from app.models.sources import Source
from app.services import embeddings as emb
from app.services.markup import strip_markup

MAX_AUTHORS = 30
_STOP = frozenset("the and for with from that this are was were has have had its into over about after new using based via".split())


def person_key(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return f"person:{slug}"


def org_key(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


async def _person(session: AsyncSession, name: str, *, create: bool = True) -> Entity | None:
    name = " ".join(name.split())
    key = person_key(name)
    entity = await session.scalar(select(Entity).where(Entity.canonical_key == key))
    if entity is None:
        alias = await session.scalar(select(EntityAlias).where(func.lower(EntityAlias.alias) == name.lower()))
        if alias is not None:
            entity = await session.get(Entity, alias.entity_id)
    if entity is None and create and len(key) > len("person:") + 2:
        entity = Entity(entity_type="PERSON", canonical_name=name, canonical_key=key, status="needs_review")
        session.add(entity)
        await session.flush()
    return entity if entity is not None and entity.entity_type == "PERSON" else None


async def _org(session: AsyncSession, name: str) -> Entity | None:
    name = " ".join(name.split())
    key = org_key(name)
    entity = await session.scalar(select(Entity).where(Entity.canonical_key == key))
    if entity is None:
        entity = Entity(entity_type="ORG", canonical_name=name, canonical_key=key, status="needs_review")
        session.add(entity)
        await session.flush()
    return entity if entity.entity_type == "ORG" else None


async def _works_at(session: AsyncSession, person: Entity, org_name: str, article_id, note: str) -> None:
    org = await _org(session, org_name)
    if org is None or org.id == person.id:
        return
    rel = await session.scalar(select(Relationship).where(Relationship.source_entity_id == person.id, Relationship.target_entity_id == org.id,
                                                          Relationship.relationship_type == "WORKS_AT"))
    if rel is None:
        rel = Relationship(source_entity_id=person.id, target_entity_id=org.id, relationship_type="WORKS_AT", confidence=0.6,
                           verification_status="unverified")
        session.add(rel)
        await session.flush()
    known = await session.scalar(select(RelationshipEvidence.id).where(RelationshipEvidence.relationship_id == rel.id,
                                                                       RelationshipEvidence.article_id == article_id))
    if known is None:
        session.add(RelationshipEvidence(relationship_id=rel.id, article_id=article_id, note=note[:300]))


async def record_authors(session: AsyncSession, article: Article, authors: list[dict] | None) -> int:
    """Authorship from the source's own metadata: document_authors, PERSON entity, WORKS_AT + evidence when an affiliation is stated."""
    written = 0
    for position, author in enumerate((authors or [])[:MAX_AUTHORS]):
        name = " ".join(str(author.get("name") or "").split())
        if len(name) < 3:
            continue
        person = await _person(session, name)
        if person is None:
            continue
        if await session.scalar(select(DocumentAuthor.id).where(DocumentAuthor.article_id == article.id, DocumentAuthor.entity_id == person.id)) is None:
            session.add(DocumentAuthor(article_id=article.id, entity_id=person.id, position=int(author.get("position", position)),
                                       affiliation=(author.get("affiliation") or None), origin="source_metadata"))
            written += 1
        if author.get("affiliation"):
            await _works_at(session, person, author["affiliation"], article.id, f"{name} is listed with affiliation '{author['affiliation']}' in the source record")
    await session.flush()
    return written


async def seed(session: AsyncSession, people: list[dict]) -> dict:
    """The supplied researcher list: PERSON entities (+ WORKS_AT where an organisation is given). No documents are invented."""
    created = existing = 0
    for item in people:
        name = " ".join(str(item["name"]).split())
        before = await _person(session, name, create=False)
        person = await _person(session, name)
        if person is None:
            continue
        created, existing = (created + 1, existing) if before is None else (created, existing + 1)
        if item.get("areas"):
            person.description = "Research areas (seed list): " + ", ".join(item["areas"])
        if item.get("organisation"):
            await _works_at(session, person, item["organisation"], None, "organisation given in the supplied seed list")
    await session.commit()
    return {"created": created, "existing": existing}


# ---- read side ---------------------------------------------------------------------------------------------------------
async def _docs_by_person(session: AsyncSession, person_ids: list | None = None) -> dict:
    stmt = (select(DocumentAuthor.entity_id, Article.id, Article.title, Article.canonical_url, Article.url, Article.published_at, Source.name, Source.tier)
            .join(Article, Article.id == DocumentAuthor.article_id).outerjoin(Source, Source.id == Article.source_id)
            .where(Article.processing_status == "accepted", Article.quality_gate_passed.is_(True))
            .order_by(Article.published_at.desc().nullslast(), Article.id))
    if person_ids:
        stmt = stmt.where(DocumentAuthor.entity_id.in_(person_ids))
    out: dict = defaultdict(list)
    for pid, aid, title, curl, url, published, source, tier in (await session.execute(stmt)).all():
        out[pid].append({"article_id": str(aid), "title": strip_markup(title), "url": curl or url, "published_at": published.isoformat() if published else None,
                         "source": source, "tier": tier})
    return out


async def _label_terms(session: AsyncSession, article_ids: list[str], labels: tuple[str, ...]) -> dict[str, Counter]:
    if not article_ids:
        return {}
    rows = (await session.execute(select(ArticleLabel.article_id, ArticleLabel.label, ArticleLabel.evidence)
                                  .where(ArticleLabel.article_id.in_(article_ids), ArticleLabel.label.in_(labels)))).all()
    out: dict[str, Counter] = defaultdict(Counter)
    for aid, label, evidence in rows:
        for term in (evidence or {}).get("terms", []):
            out[str(aid)][f"{label}:{term}"] += 1
    return out


async def _org_of(session: AsyncSession, person_id) -> list[dict]:
    rows = (await session.execute(select(Entity.canonical_name, Relationship.verification_status, func.count(RelationshipEvidence.article_id))
                                  .join(Relationship, Relationship.target_entity_id == Entity.id)
                                  .outerjoin(RelationshipEvidence, RelationshipEvidence.relationship_id == Relationship.id)
                                  .where(Relationship.source_entity_id == person_id, Relationship.relationship_type == "WORKS_AT")
                                  .group_by(Entity.canonical_name, Relationship.verification_status).order_by(func.count(RelationshipEvidence.article_id).desc(), Entity.canonical_name))).all()
    return [{"organisation": n, "verification_status": v, "evidence_count": c} for n, v, c in rows]


async def _summary(session: AsyncSession, person: Entity, docs: list[dict]) -> dict:
    terms = await _label_terms(session, [d["article_id"] for d in docs], ("technology", "ai_model", "research", "industry"))
    areas: Counter = Counter()
    for c in terms.values():
        areas.update(c)
    clients = []
    if docs:
        rows = (await session.execute(select(Entity.canonical_name).distinct().join(EntityMention, EntityMention.entity_id == Entity.id)
                                      .where(EntityMention.article_id.in_([d["article_id"] for d in docs]), Entity.entity_type == "ORG",
                                             Entity.canonical_key.in_([n.lower() for n in CLIENT_CONFIG])))).all()
        clients = sorted(r[0] for r in rows)
    return {"person_id": str(person.id), "name": person.canonical_name, "seed_note": person.description,
            "organisations": await _org_of(session, person.id), "documents": len(docs),
            "latest_document": docs[0]["published_at"] if docs else None,
            "areas": [{"term": t.split(":", 1)[1], "kind": t.split(":", 1)[0], "documents": n} for t, n in areas.most_common(8)],
            "linked_clients": clients,
            **({"evidence_status": "NO_STORED_DOCUMENTS", "detail": "A seed-list person with no stored document: listed for tracking, ranked by evidence only."} if not docs else {})}


async def list_researchers(session: AsyncSession, *, q: str | None = None, limit: int = 25, offset: int = 0) -> dict:
    stmt = select(Entity).where(Entity.entity_type == "PERSON")
    if q:
        stmt = stmt.where(func.lower(Entity.canonical_name).contains(" ".join(q.split()).lower(), autoescape=True))
    persons = (await session.scalars(stmt)).all()
    docs = await _docs_by_person(session, [p.id for p in persons] or None) if persons else {}
    ranked = sorted(persons, key=lambda p: (-len(docs.get(p.id, [])), p.canonical_name.lower()))  # evidence first; seed-only people last
    page = ranked[offset:offset + limit]
    items = [await _summary(session, p, docs.get(p.id, [])) for p in page]
    return {"total": len(ranked), "items": items,
            **({"status": "NO_RESEARCHERS_STORED", "detail": "No PERSON entities exist yet: they appear when a research source records authors or when a seed list is supplied."} if not ranked else {})}


async def researcher_detail(session: AsyncSession, person_id) -> dict | None:
    person = await session.get(Entity, person_id)
    if person is None or person.entity_type != "PERSON":
        return None
    docs = (await _docs_by_person(session, [person.id])).get(person.id, [])
    return {**await _summary(session, person, docs), "recent_work": docs[:20]}


def _tokens(text_: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9][a-z0-9\-]{2,}", text_.lower()) if t not in _STOP}


async def _person_vectors(session: AsyncSession, provider) -> dict:
    rows = (await session.execute(text(
        "select da.entity_id, c.embedding::text from document_authors da join document_chunks c on c.article_id = da.article_id "
        "join articles a on a.id = da.article_id where c.embedding is not null and c.embedding_model = :m and a.processing_status = 'accepted' "
        "and c.field in ('title', 'summary')"), {"m": provider.model})).all()
    sums: dict = {}
    counts: Counter = Counter()
    for pid, vec in rows:
        values = [float(x) for x in vec.strip("[]").split(",")]
        sums[pid] = [a + b for a, b in zip(sums[pid], values)] if pid in sums else values
        counts[pid] += 1
    return {pid: [x / counts[pid] for x in v] for pid, v in sums.items()}


def _cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


async def similar_researchers(session: AsyncSession, person_id, *, context: str | None = None, limit: int = 5) -> dict | None:
    target = await session.get(Entity, person_id)
    if target is None or target.entity_type != "PERSON":
        return None
    docs = await _docs_by_person(session)
    if not docs.get(target.id):
        return {"person": target.canonical_name, "method": None, "results": [], "status": "NO_STORED_DOCUMENTS",
                "detail": "Similarity is computed from a person's stored recent work; this person has none."}
    provider = emb.get_provider()
    vectors = await _person_vectors(session, provider) if provider is not None else {}
    method = "embedding_nearest_neighbour" if target.id in vectors and len(vectors) > 1 else "term_overlap"
    all_ids = [pid for pid in docs if pid != target.id]
    label_terms = {pid: await _label_terms(session, [d["article_id"] for d in docs[pid]], ("technology", "ai_model", "research")) for pid in [target.id, *all_ids]}

    def profile_terms(pid) -> set[str]:
        terms = set()
        for counter in label_terms[pid].values():
            terms |= {t.split(":", 1)[1].lower() for t in counter}
        for d in docs[pid]:
            terms |= _tokens(d["title"] or "")
        return terms
    mine = profile_terms(target.id)
    scored = []
    for pid in all_ids:
        if method == "embedding_nearest_neighbour":
            if pid not in vectors:
                continue
            score = _cosine(vectors[target.id], vectors[pid])
        else:
            theirs = profile_terms(pid)
            score = len(mine & theirs) / len(mine | theirs) if mine | theirs else 0.0
        if score > 0:
            scored.append((score, pid))
    scored.sort(key=lambda s: (-s[0], str(s[1])))
    results = []
    ctx_tokens = _tokens(context) if context else set()
    for score, pid in scored[:limit]:
        person = await session.get(Entity, pid)
        summary = await _summary(session, person, docs[pid])
        relevant = []
        if ctx_tokens:
            for d in docs[pid]:
                hit = sorted(ctx_tokens & _tokens(d["title"] or ""))
                if hit:
                    relevant.append({"article_id": d["article_id"], "title": d["title"], "url": d["url"], "matched_terms": hit})
        results.append({**summary, "similarity": round(score, 4), "recent_work": docs[pid][:3],
                        "shared_terms": sorted(mine & profile_terms(pid))[:8] if method == "term_overlap" else [],
                        "relevance_in_context": ({"context": context, "documents": relevant[:3]} if relevant else
                                                 ({"context": context, "documents": [], "detail": "No stored document of this person states the context terms."} if context else None))})
    return {"person": target.canonical_name, "method": method, "context": context, "results": results,
            **({} if results else {"status": "NO_SIMILAR_RESEARCHERS", "detail": "No other researcher shares stored work with this person."})}
