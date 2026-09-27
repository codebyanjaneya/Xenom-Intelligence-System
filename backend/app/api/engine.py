"""Engine operations: capability status, indexing (chunks, embeddings, labels), AI usage and cost (PDF s.10, s.13).

Everything that writes is admin-only and explicit; nothing here runs implicitly against stored data.
"""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func as sa_func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_admin
from app.config import taxonomy
from app.db.session import get_session
from app.services import chunking, embeddings
from app.services import llm as llm_service
from app.services import evaluation_store as evalstore
from app.services import fact_check
from app.services import fine_tuning
from app.services import materialize
from app.services import recall_recovery as recall
from app.services import recommendations as recs
from app.services import taxonomy as taxonomy_service
from app.models import User
from app.schemas.fact_check import FactCheckRequest, FactCheckResult

router = APIRouter(prefix="/engine", tags=["engine"])


@router.post("/fact-check", response_model=FactCheckResult, dependencies=[Depends(get_current_user)])
async def run_fact_check(payload: FactCheckRequest, session: AsyncSession = Depends(get_session)) -> FactCheckResult:
    """Given a headline/URL/claim, search the stored evidenced corpus for corroboration and, if an LLM is
    configured, get a verdict grounded only in that corroboration or a clear red flag in the text itself."""
    clean = payload.input.strip()
    if not clean:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="input must not be empty")
    return await fact_check.check_news(session, clean)


@router.get("/capabilities", dependencies=[Depends(get_current_user)])
async def capabilities(session: AsyncSession = Depends(get_session)) -> dict:
    """What is real in this deployment: each optional component reports configured / not_configured with the reason."""
    stats = await chunking.chunk_stats(session)
    labels = (await session.execute(text("select count(*), count(distinct article_id) from article_labels"))).one()
    return {
        "lexical_search": {"status": "active", "index": "articles.search_vector (GIN) + pg_trgm"},
        "embeddings": {**embeddings.capability(), "chunks": stats["chunks"], "embedded_chunks": stats["embedded"]},
        "hybrid_search": {"status": "active" if embeddings.get_provider() and stats["embedded"] else "lexical_fallback",
                          "detail": None if embeddings.get_provider() and stats["embedded"] else "requires a configured embedding provider and embedded chunks"},
        "llm": llm_service.capability(),
        "taxonomy": {"status": "active", "version": taxonomy.TAXONOMY_VERSION, "labels": list(taxonomy.LABELS),
                     "labelled_articles": labels[1], "label_rows": labels[0]},
        "fine_tuning": fine_tuning.fine_tuning_capability(),
    }


@router.post("/index/chunks", dependencies=[Depends(require_admin)])
async def index_chunks(limit: int = Query(default=1000, ge=1, le=20000), session: AsyncSession = Depends(get_session)) -> dict:
    return await chunking.backfill_chunks(session, limit=limit)


@router.post("/index/embed", dependencies=[Depends(require_admin)])
async def index_embeddings(limit: int = Query(default=500, ge=1, le=5000), session: AsyncSession = Depends(get_session)) -> dict:
    return await embeddings.embed_pending(session, limit=limit)


@router.post("/index/labels", dependencies=[Depends(require_admin)])
async def index_labels(limit: int = Query(default=1000, ge=1, le=20000), relabel: bool = False,
                       session: AsyncSession = Depends(get_session)) -> dict:
    return await taxonomy_service.backfill(session, limit=limit, only_unlabelled=not relabel)


@router.get("/ai-usage", dependencies=[Depends(get_current_user)])
async def ai_usage(session: AsyncSession = Depends(get_session)) -> dict:
    """Operational metrics for every embedding / LLM call: count, failures, latency, tokens and cost by operation."""
    rows = (await session.execute(text(
        "select provider, model, operation, count(*), count(*) filter (where status = 'ok'), count(*) filter (where status is distinct from 'ok'), "
        "coalesce(round(avg(latency_ms)), 0), coalesce(sum(total_tokens), 0), coalesce(sum(cost_usd), 0) "
        "from ai_usage group by provider, model, operation order by operation, model"))).all()
    recent_errors = (await session.execute(text(
        "select created_at, provider, model, operation, error from ai_usage where status is distinct from 'ok' order by created_at desc limit 10"))).all()
    return {
        "by_operation": [{"provider": r[0], "model": r[1], "operation": r[2], "calls": r[3], "ok": r[4], "failed": r[5],
                          "avg_latency_ms": int(r[6]), "tokens": int(r[7]), "cost_usd": float(r[8])} for r in rows],
        "recent_errors": [{"at": r[0].isoformat(), "provider": r[1], "model": r[2], "operation": r[3], "error": r[4]} for r in recent_errors],
        "total_calls": sum(r[3] for r in rows), "total_cost_usd": round(sum(float(r[8]) for r in rows), 6),
    }


# ---- LLM enrichment (admin, explicit, one article / one client at a time; never bulk-runs on the corpus) ---------------
def _llm(call):
    async def run(*a, **kw):
        try:
            return await call(*a, **kw)
        except llm_service.LLMUnavailable as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
        except llm_service.LLMOutputInvalid as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
        except LookupError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return run


class SynthesisRequest(BaseModel):
    client: str = Field(max_length=40)
    kind: Literal["insight", "recommendation"] = "insight"


@router.post("/llm/adjudicate/{article_id}", dependencies=[Depends(require_admin)])
async def llm_adjudicate(article_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> dict:
    return await _llm(llm_service.adjudicate_relevance)(session, article_id)


@router.post("/llm/claims/{article_id}", dependencies=[Depends(require_admin)])
async def llm_claims(article_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> dict:
    return await _llm(llm_service.extract_claims)(session, article_id)


@router.post("/llm/labels/{article_id}", dependencies=[Depends(require_admin)])
async def llm_labels(article_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> dict:
    return await _llm(llm_service.classify_gaps)(session, article_id)


@router.post("/llm/synthesize", dependencies=[Depends(require_admin)])
async def llm_synthesize(payload: SynthesisRequest, session: AsyncSession = Depends(get_session)) -> dict:
    from app.config.clients import CLIENT_CONFIG
    name = next((n for n in CLIENT_CONFIG if n.lower() == payload.client.strip().lower()), None)
    if name is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown client")
    return await _llm(llm_service.synthesize)(session, name, kind=payload.kind)


# ---- recall recovery: the only route back for rejected items ------------------------------------------------------------
class RecallScan(BaseModel):
    client: str | None = Field(default=None, max_length=40)
    limit: int = Field(default=500, ge=1, le=5000)


class RecallDecision(BaseModel):
    accept: bool


@router.post("/recall/scan", dependencies=[Depends(require_admin)])
async def recall_scan(payload: RecallScan, session: AsyncSession = Depends(get_session)) -> dict:
    from app.services.profiles import UnknownProfileClient
    try:
        return await recall.scan(session, client=payload.client, limit=payload.limit)
    except UnknownProfileClient:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown client")


@router.get("/recall", dependencies=[Depends(get_current_user)])
async def recall_list(status_filter: Literal["open", "resolved", "dismissed"] = Query(default="open", alias="status"),
                      session: AsyncSession = Depends(get_session)) -> list[dict]:
    return await recall.list_candidates(session, status_filter)


@router.post("/recall/{review_id}")
async def recall_decide(review_id: uuid.UUID, payload: RecallDecision, user: User = Depends(require_admin),
                        session: AsyncSession = Depends(get_session)) -> dict:
    try:
        return await recall.decide(session, review_id, accept=payload.accept, actor=user.email)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except recall.RecoveryError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


# ---- recommended actions (draft -> reviewed -> published) ---------------------------------------------------------------
class RecSync(BaseModel):
    client: str = Field(max_length=40)


class RecStatus(BaseModel):
    status: Literal["draft", "reviewed", "published", "dismissed"]


def _rec_out(r) -> dict:
    return {"id": str(r.id), "client": r.client_key, "opportunity_key": r.opportunity_key, "title": r.title, "action": r.action,
            "rationale": r.rationale, "confidence": r.confidence, "confidence_basis": r.confidence_basis, "article_ids": r.article_ids,
            "claim_ids": r.claim_ids, "origin": r.origin, "generated": r.generated, "status": r.status, "reviewed_by": r.reviewed_by,
            "created_at": r.created_at.isoformat()}


@router.post("/recommendations/sync", dependencies=[Depends(require_admin)])
async def recommendations_sync(payload: RecSync, session: AsyncSession = Depends(get_session)) -> dict:
    from app.services.client_engine import UnknownClient
    try:
        return await recs.sync_client(session, payload.client)
    except UnknownClient:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown client")


@router.get("/recommendations", dependencies=[Depends(get_current_user)])
async def recommendations_list(client: str | None = Query(default=None, max_length=40),
                               status_filter: Literal["draft", "reviewed", "published", "dismissed"] | None = Query(default=None, alias="status"),
                               session: AsyncSession = Depends(get_session)) -> list[dict]:
    from sqlalchemy import select
    from app.models.architecture import Recommendation
    stmt = select(Recommendation).order_by(Recommendation.created_at.desc(), Recommendation.id)
    if client:
        stmt = stmt.where(Recommendation.client_key == client.strip().lower())
    if status_filter:
        stmt = stmt.where(Recommendation.status == status_filter)
    return [_rec_out(r) for r in (await session.scalars(stmt)).all()]


@router.patch("/recommendations/{rec_id}")
async def recommendation_status(rec_id: uuid.UUID, payload: RecStatus, user: User = Depends(require_admin),
                                session: AsyncSession = Depends(get_session)) -> dict:
    from app.models.architecture import Recommendation
    rec = await session.get(Recommendation, rec_id)
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recommendation not found")
    rec.status, rec.reviewed_by = payload.status, user.email
    await session.commit()
    return _rec_out(rec)


# ---- Xenom capability data (input to capability matching; nothing is presented as confirmed unless VERIFIED) ---------
class CapabilityIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    category: str | None = Field(default=None, max_length=80)
    verification_status: Literal["UNVERIFIED", "VERIFIED"] = "UNVERIFIED"
    source_note: str | None = Field(default=None, max_length=500)


@router.get("/xenom/capabilities", dependencies=[Depends(get_current_user)])
async def list_capabilities(session: AsyncSession = Depends(get_session)) -> list[dict]:
    from sqlalchemy import select
    from app.models.capability import XenomCapability
    rows = (await session.scalars(select(XenomCapability).order_by(XenomCapability.name))).all()
    return [{"id": str(r.id), "name": r.name, "description": r.description, "category": r.category,
             "verification_status": r.verification_status, "source_note": r.source_note} for r in rows]


@router.put("/xenom/capabilities", dependencies=[Depends(require_admin)])
async def upsert_capability(payload: CapabilityIn, session: AsyncSession = Depends(get_session)) -> dict:
    from sqlalchemy import select
    from app.models.capability import XenomCapability
    row = await session.scalar(select(XenomCapability).where(XenomCapability.name == payload.name))
    if row is None:
        row = XenomCapability(name=payload.name)
        session.add(row)
    row.description, row.category, row.verification_status, row.source_note = (payload.description, payload.category, payload.verification_status, payload.source_note)
    await session.commit()
    return {"id": str(row.id), "name": row.name, "verification_status": row.verification_status}


# ---- evaluation (human labels only; the system under test is never its own ground truth) --------------------------------
class EvalSetIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    kind: Literal["retrieval", "relevance", "taxonomy"]
    description: str | None = Field(default=None, max_length=500)


class EvalLabelIn(BaseModel):
    subject_key: str = Field(min_length=1, max_length=500)  # retrieval: query; relevance: client; taxonomy: article id
    item_key: str | None = Field(default=None, max_length=200)  # retrieval/relevance: article id; taxonomy: label name
    label: str = Field(min_length=1, max_length=40)
    grade: int | None = Field(default=None, ge=0, le=3)


class EvalRun(BaseModel):
    modes: list[Literal["lexical", "semantic", "hybrid"]] | None = None


def _eval(call):
    async def run(*a, **kw):
        try:
            return await call(*a, **kw)
        except evalstore.EvaluationError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
        except LookupError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return run


@router.post("/evaluation/sets", status_code=status.HTTP_201_CREATED)
async def evaluation_create_set(payload: EvalSetIn, user: User = Depends(require_admin), session: AsyncSession = Depends(get_session)) -> dict:
    row = await _eval(evalstore.create_set)(session, name=payload.name, kind=payload.kind, description=payload.description, actor=user.email)
    return {"id": str(row.id), "name": row.name, "kind": row.kind}


@router.get("/evaluation/sets", dependencies=[Depends(get_current_user)])
async def evaluation_sets(session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = (await session.execute(text(
        "select s.id, s.name, s.kind, s.description, (select count(*) from evaluation_labels l where l.set_id = s.id), "
        "(select count(*) from evaluation_runs r where r.set_id = s.id) from evaluation_sets s order by s.name"))).all()
    return [{"id": str(r[0]), "name": r[1], "kind": r[2], "description": r[3], "labels": r[4], "runs": r[5]} for r in rows]


@router.put("/evaluation/sets/{set_id}/labels")
async def evaluation_labels(set_id: uuid.UUID, labels: list[EvalLabelIn], user: User = Depends(require_admin),
                            session: AsyncSession = Depends(get_session)) -> dict:
    return {"upserted": await _eval(evalstore.upsert_labels)(session, set_id, [item.model_dump() for item in labels], actor=user.email)}


@router.post("/evaluation/sets/{set_id}/run", dependencies=[Depends(require_admin)])
async def evaluation_run(set_id: uuid.UUID, payload: EvalRun, user: User = Depends(require_admin), session: AsyncSession = Depends(get_session)) -> dict:
    return await _eval(evalstore.run_set)(session, set_id, modes=payload.modes, actor=user.email)


@router.get("/evaluation/runs", dependencies=[Depends(get_current_user)])
async def evaluation_runs(set_id: uuid.UUID | None = None, limit: int = Query(default=20, ge=1, le=100),
                          session: AsyncSession = Depends(get_session)) -> list[dict]:
    from sqlalchemy import select
    from app.models.architecture import EvaluationRun as RunRow
    stmt = select(RunRow).order_by(RunRow.created_at.desc(), RunRow.id).limit(limit)
    if set_id:
        stmt = stmt.where(RunRow.set_id == set_id)
    return [{"id": str(r.id), "set_id": str(r.set_id) if r.set_id else None, "kind": r.kind, "mode": r.mode, "metrics": r.metrics,
             "corpus_digest": r.corpus_digest, "created_at": r.created_at.isoformat()} for r in (await session.scalars(stmt)).all()]


@router.get("/evaluation/operations", dependencies=[Depends(get_current_user)])
async def evaluation_operations(session: AsyncSession = Depends(get_session)) -> dict:
    return await evalstore.operations_metrics(session)


class FineTuneDatasetIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    kind: Literal["taxonomy_labels", "relevance_adjudication"]
    label: str | None = None
    limit: int = Field(default=5000, ge=1, le=50000)


class FineTuneJobIn(BaseModel):
    dataset_id: uuid.UUID
    base_model: str = Field(min_length=1, max_length=200)
    config: dict = Field(default_factory=dict)


def _ft(call):
    async def run(*a, **kw):
        try:
            return await call(*a, **kw)
        except fine_tuning.FineTuningError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return run


@router.post("/fine-tuning/datasets", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
async def fine_tuning_create_dataset(payload: FineTuneDatasetIn, user: User = Depends(require_admin),
                                     session: AsyncSession = Depends(get_session)) -> dict:
    row = await _ft(fine_tuning.build_dataset)(session, name=payload.name, kind=payload.kind, label=payload.label,
                                                limit=payload.limit, actor=user.email)
    return {"id": str(row.id), "name": row.name, "kind": row.kind, "item_count": row.item_count, "digest": row.digest}


@router.get("/fine-tuning/datasets", dependencies=[Depends(get_current_user)])
async def fine_tuning_list_datasets(session: AsyncSession = Depends(get_session)) -> list[dict]:
    return [{"id": str(r.id), "name": r.name, "kind": r.kind, "item_count": r.item_count, "digest": r.digest,
             "created_by": r.created_by, "created_at": r.created_at.isoformat()} for r in await fine_tuning.list_datasets(session)]


@router.post("/fine-tuning/jobs", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
async def fine_tuning_create_job(payload: FineTuneJobIn, user: User = Depends(require_admin),
                                 session: AsyncSession = Depends(get_session)) -> dict:
    job = await _ft(fine_tuning.create_job)(session, dataset_id=payload.dataset_id, base_model=payload.base_model,
                                            config=payload.config, actor=user.email)
    return {"id": str(job.id), "dataset_id": str(job.dataset_id), "base_model": job.base_model, "status": job.status,
            "status_reason": job.status_reason}


@router.get("/fine-tuning/jobs", dependencies=[Depends(get_current_user)])
async def fine_tuning_list_jobs(session: AsyncSession = Depends(get_session)) -> list[dict]:
    return [{"id": str(r.id), "dataset_id": str(r.dataset_id), "base_model": r.base_model, "status": r.status,
             "status_reason": r.status_reason, "provider": r.provider, "created_at": r.created_at.isoformat()}
            for r in await fine_tuning.list_jobs(session)]


@router.get("/near-duplicates", dependencies=[Depends(get_current_user)])
async def near_duplicates(threshold: float = Query(default=0.93, ge=0.5, le=1.0), limit: int = Query(default=50, ge=1, le=200),
                          session: AsyncSession = Depends(get_session)) -> dict:
    from app.services.retrieval import near_duplicates as find
    return await find(session, threshold=threshold, limit=limit)


# ---- persisted derived intelligence: draft -> published / archived (reviewed by a person) -------------------------------
class MaterializeRequest(BaseModel):
    client: str | None = Field(default=None, max_length=40)


class InsightStatus(BaseModel):
    status: Literal["draft", "published", "archived"]


@router.post("/materialize", dependencies=[Depends(require_admin)])
async def materialize_intelligence(payload: MaterializeRequest, session: AsyncSession = Depends(get_session)) -> list[dict]:
    """Persist the current rule-derived insights (as drafts), opportunities and alerts. Idempotent; human decisions are kept."""
    from app.services.client_engine import UnknownClient
    try:
        return [await materialize.materialize_client(session, payload.client)] if payload.client else await materialize.materialize_all(session)
    except UnknownClient:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown client")


@router.get("/insights", dependencies=[Depends(get_current_user)])
async def stored_insights(client: str | None = Query(default=None, max_length=40),
                          status_filter: Literal["draft", "published", "archived"] | None = Query(default=None, alias="status"),
                          limit: int = Query(default=50, ge=1, le=200), session: AsyncSession = Depends(get_session)) -> list[dict]:
    from sqlalchemy import select
    from app.models import Insight
    from app.models.entities import Entity
    stmt = (select(Insight, Entity.canonical_name).outerjoin(Entity, Entity.id == Insight.client_entity_id)
            .order_by(Insight.priority.desc().nullslast(), Insight.created_at.desc(), Insight.id).limit(limit))
    if client:
        stmt = stmt.where(sa_func.lower(Entity.canonical_key) == client.strip().lower())
    if status_filter:
        stmt = stmt.where(Insight.status == status_filter)
    return [{"id": str(i.id), "client": name, "title": i.title, "body": i.body, "insight_type": i.insight_type, "priority": i.priority,
             "status": i.status, "generated": i.insight_type == "llm_synthesis", "created_at": i.created_at.isoformat()}
            for i, name in (await session.execute(stmt)).all()]


@router.patch("/insights/{insight_id}", dependencies=[Depends(require_admin)])
async def set_insight_status(insight_id: uuid.UUID, payload: InsightStatus, session: AsyncSession = Depends(get_session)) -> dict:
    from app.models import Insight
    row = await session.get(Insight, insight_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Insight not found")
    row.status = payload.status
    await session.commit()
    return {"id": str(row.id), "status": row.status}
