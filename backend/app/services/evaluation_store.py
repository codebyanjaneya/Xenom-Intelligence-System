"""Reproducible evaluation (PDF s.13, s.14): evaluation_sets, evaluation_labels (human judgements only), evaluation_runs.

The system under test is never its own ground truth: labels are written by a person through the API, runs compare the engine
with those labels, and a metric with no labels is reported as ``NOT_MEASURED``. Retrieval sets are run per mode (lexical,
semantic, hybrid) on the same judged queries so the modes can be compared; a mode that fell back to lexical says so.
``operations_metrics`` needs no labels: it measures operational facts (fetch/parse success, duplicate rate, gate rejection
reasons, LLM cost and latency, claim quote support, citation validity, human accept/dismiss rate).
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Claim, ReviewItem
from app.models.architecture import ArticleLabel, EvaluationLabel, EvaluationRun, EvaluationSet, ProfileSuggestion, Recommendation
from app.models.content import Article
from app.models.entities import Entity, EntityMention
from app.services import retrieval
from app.services.claims import ClaimService
from evaluation.relevance import Metrics, compute_metrics
from evaluation.search_quality import DEFAULT_KS, aggregate, evaluate_query

NOT_MEASURED = "not measured"
KINDS = ("retrieval", "relevance", "taxonomy")


class EvaluationError(ValueError):
    pass


async def create_set(session: AsyncSession, *, name: str, kind: str, description: str | None, actor: str | None) -> EvaluationSet:
    if kind not in KINDS:
        raise EvaluationError(f"kind must be one of {KINDS}")
    if await session.scalar(select(EvaluationSet.id).where(EvaluationSet.name == name)):
        raise EvaluationError("an evaluation set with this name exists")
    row = EvaluationSet(name=name, kind=kind, description=description, created_by=actor)
    session.add(row)
    await session.commit()
    return row


async def upsert_labels(session: AsyncSession, set_id, labels: list[dict], *, actor: str | None) -> int:
    ev_set = await session.get(EvaluationSet, set_id)
    if ev_set is None:
        raise LookupError("evaluation set not found")
    count = 0
    for item in labels:
        existing = await session.scalar(select(EvaluationLabel).where(EvaluationLabel.set_id == set_id, EvaluationLabel.subject_key == item["subject_key"],
                                                                       EvaluationLabel.item_key == item.get("item_key")))
        if existing is None:
            existing = EvaluationLabel(set_id=set_id, subject_key=item["subject_key"], item_key=item.get("item_key"), label=item["label"])
            session.add(existing)
        existing.label, existing.grade, existing.labeled_by = item["label"], item.get("grade"), actor
        count += 1
    await session.commit()
    return count


async def _digest(session: AsyncSession) -> str:
    ids = (await session.scalars(select(Article.id).where(Article.processing_status == "accepted").order_by(Article.id))).all()
    return hashlib.sha256("\n".join(str(i) for i in ids).encode()).hexdigest()[:16]


async def _labels(session: AsyncSession, set_id) -> list[EvaluationLabel]:
    return list((await session.scalars(select(EvaluationLabel).where(EvaluationLabel.set_id == set_id).order_by(EvaluationLabel.subject_key, EvaluationLabel.item_key))).all())


async def run_set(session: AsyncSession, set_id, *, modes: list[str] | None = None, actor: str | None = None) -> dict:
    ev_set = await session.get(EvaluationSet, set_id)
    if ev_set is None:
        raise LookupError("evaluation set not found")
    labels = await _labels(session, set_id)
    digest = await _digest(session)
    results = []
    if ev_set.kind == "retrieval":
        for mode in modes or ["lexical", "semantic", "hybrid"]:
            metrics, used = await _run_retrieval(session, labels, mode)
            results.append((mode, {**metrics, "mode_used": used}))
    elif ev_set.kind == "relevance":
        results.append((None, await _run_relevance(session, labels)))
    else:
        results.append((None, await _run_taxonomy(session, labels)))
    runs = []
    for mode, metrics in results:
        row = EvaluationRun(set_id=set_id, kind=ev_set.kind, mode=mode, metrics=metrics, corpus_digest=digest)
        session.add(row)
        runs.append(row)
    await session.commit()
    return {"set": ev_set.name, "kind": ev_set.kind, "corpus_digest": digest, "labels": len(labels),
            "runs": [{"run_id": str(r.id), "mode": r.mode, "metrics": r.metrics} for r in runs]}


async def _run_retrieval(session: AsyncSession, labels: list[EvaluationLabel], mode: str) -> tuple[dict, str]:
    queries: dict[str, set] = {}
    for label in labels:
        expected = queries.setdefault(label.subject_key, set())
        if label.item_key and (label.grade if label.grade is not None else 1) >= 1 and label.label != "not_relevant":
            expected.add(label.item_key)
    per_query, used = [], Counter()
    for i, (query, expected) in enumerate(sorted(queries.items())):
        response = await retrieval.search(session, query, mode=mode, limit=max(DEFAULT_KS))
        used[response.mode] += 1
        ranked = [str(h.article_id) for h in response.results]
        per_query.append(evaluate_query({"id": f"q{i + 1}", "query": query, "category": None, "labelled": True, "expected_article_ids": sorted(expected)}, ranked))
    if not per_query:
        return {"status": NOT_MEASURED, "detail": "no labelled queries"}, mode
    return {"per_query": per_query, "aggregate": aggregate(per_query), "fallbacks_to_lexical": used.get("lexical", 0) if mode != "lexical" else 0}, (
        mode if used.get(mode) else "lexical")


async def _run_relevance(session: AsyncSession, labels: list[EvaluationLabel]) -> dict:
    rows = []
    for label in labels:
        if label.label not in ("relevant", "not_relevant") or not label.item_key:
            continue
        article = await session.get(Article, label.item_key)
        if article is None:
            continue
        entity = await session.scalar(select(Entity).where(Entity.canonical_key == label.subject_key.lower(), Entity.entity_type == "ORG"))
        mentioned = entity is not None and await session.scalar(select(EntityMention.id).where(EntityMention.article_id == article.id, EntityMention.entity_id == entity.id)) is not None
        rows.append({"client": label.subject_key, "current_decision": "accept" if article.processing_status == "accepted" and mentioned else "reject",
                     "human_label": "accept" if label.label == "relevant" else "reject", "stored_status": article.processing_status})
    if not rows:
        return {"status": NOT_MEASURED, "detail": "no human relevance labels"}
    overall = compute_metrics(rows)
    per_client = {}
    for client in sorted({r["client"] for r in rows}):
        m = compute_metrics([r for r in rows if r["client"] == client])
        per_client[client] = m.as_dict() if isinstance(m, Metrics) else NOT_MEASURED
    rejected = [r for r in rows if r["stored_status"] == "rejected"]
    return {"overall": overall.as_dict() if isinstance(overall, Metrics) else NOT_MEASURED, "per_client": per_client,
            "wrongly_rejected_estimate": {"labelled_rejected": len(rejected), "human_says_relevant": sum(r["human_label"] == "accept" for r in rejected)}
            if rejected else NOT_MEASURED}


async def _run_taxonomy(session: AsyncSession, labels: list[EvaluationLabel]) -> dict:
    per_label: dict[str, Counter] = {}
    for label in labels:
        if label.label not in ("yes", "no") or not label.item_key:
            continue
        predicted = await session.scalar(select(ArticleLabel.id).where(ArticleLabel.article_id == label.subject_key, ArticleLabel.label == label.item_key).limit(1)) is not None
        c = per_label.setdefault(label.item_key, Counter())
        c["tp" if predicted and label.label == "yes" else "fp" if predicted else "fn" if label.label == "yes" else "tn"] += 1
    if not per_label:
        return {"status": NOT_MEASURED, "detail": "no human taxonomy labels"}

    def ratio(a, b):
        return round(a / b, 4) if b else NOT_MEASURED
    return {"per_label": {name: {"n": sum(c.values()), "precision": ratio(c["tp"], c["tp"] + c["fp"]), "recall": ratio(c["tp"], c["tp"] + c["fn"]),
                                 "tp": c["tp"], "fp": c["fp"], "fn": c["fn"], "tn": c["tn"]} for name, c in sorted(per_label.items())}}


async def operations_metrics(session: AsyncSession) -> dict:
    """Operational facts measured from stored data. Nothing here needs labels; accuracy claims are not made."""
    fetch = dict((await session.execute(text("select status, count(*) from source_health group by status"))).all())
    total_fetch = sum(fetch.values())
    statuses = dict((await session.execute(text("select processing_status, count(*) from articles group by processing_status"))).all())
    reasons = Counter()
    for (raw,) in (await session.execute(text("select score_reason from articles where processing_status = 'rejected'"))).all():
        try:
            data = json.loads(raw or "{}")
            reasons[f"{data.get('stage')}:{data.get('reason')}"] += 1
        except ValueError:
            reasons["unparseable"] += 1
    usage = (await session.execute(text(
        "select count(*), count(*) filter (where status = 'ok'), coalesce(round(avg(latency_ms)), 0), coalesce(sum(cost_usd), 0), coalesce(sum(total_tokens), 0) "
        "from ai_usage where operation like 'llm:%'"))).one()
    claims = await ClaimService(session=session).list()
    resolvable = sum(c.provenance_status == "VERIFIED_IN_STORED_CORPUS" for c in claims)
    rec_rows = dict((await session.execute(select(Recommendation.status, func.count()).group_by(Recommendation.status))).all())
    decided = rec_rows.get("reviewed", 0) + rec_rows.get("published", 0) + rec_rows.get("dismissed", 0)
    llm_recs = (await session.scalars(select(Recommendation).where(Recommendation.origin == "llm"))).all()
    valid_claim_ids = set(str(i) for i in (await session.scalars(select(Claim.id))).all())
    cited = [cid for r in llm_recs for cid in (r.claim_ids or [])]
    suggestions = dict((await session.execute(select(ProfileSuggestion.status, func.count()).group_by(ProfileSuggestion.status))).all())
    recall = dict((await session.execute(select(ReviewItem.status, func.count()).where(ReviewItem.object_type.in_(("recall_candidate", "llm_adjudication")))
                                         .group_by(ReviewItem.status))).all())
    accepted, rejected = statuses.get("accepted", 0), statuses.get("rejected", 0)
    dup_rejected = reasons.get("duplicate:DUPLICATE_OF_ACCEPTED", 0)

    def ratio(a, b):
        return round(a / b, 4) if b else NOT_MEASURED
    return {
        "accuracy_note": "These are operational measurements, not accuracy: precision/recall need human labels (evaluation sets).",
        "fetch": {"attempts": total_fetch, "ok": fetch.get("ok", 0), "error": fetch.get("error", 0), "timeout": fetch.get("timeout", 0),
                  "success_rate": ratio(fetch.get("ok", 0), total_fetch)},
        "pipeline": {"accepted": accepted, "rejected": rejected, "duplicate_rate": ratio(dup_rejected, accepted + rejected),
                     "quality_gate_rejection_reasons": dict(reasons.most_common(12))},
        "llm": {"calls": usage[0], "ok": usage[1], "avg_latency_ms": int(usage[2]), "cost_usd": float(usage[3]), "tokens": int(usage[4])},
        "evidence": {"claims": len(claims), "claim_quote_support_rate": ratio(resolvable, len(claims)),
                     "llm_recommendation_citation_validity": ratio(sum(c in valid_claim_ids for c in cited), len(cited)),
                     "unsupported_sentence_rate": NOT_MEASURED},  # uncited sentences are removed before storage, so none is stored to be counted
        "human_review": {"recommendations": rec_rows, "recommendation_accept_rate": ratio(rec_rows.get("reviewed", 0) + rec_rows.get("published", 0), decided),
                         "suggestions": suggestions, "recall_candidates": recall,
                         "recall_accept_rate": ratio(recall.get("resolved", 0), recall.get("resolved", 0) + recall.get("dismissed", 0))},
    }
