"""Fine-tuning architecture (manager-requested extension; PDF s.16 open decision).

Real, extensible scaffolding only: dataset export from stored evidence, job records with config/version, and
evaluation hooks reusing the existing evaluation infrastructure (Section 13). This module never trains a model
and never fabricates a result. With no fine-tuning provider configured, every job is created ``blocked`` and
stays there; a real provider integration is the only thing that can move a job forward.

Datasets are built only from real, already-reviewed stored data:
  - ``taxonomy_labels``: article_labels rows (rule/human method), for a taxonomy classifier.
  - ``relevance_adjudication``: claims with a resolved verification_status, for a relevance/adjudication model.
No synthetic examples are ever generated.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.architecture import ArticleLabel, FineTuneDataset, FineTuneJob
from app.models.evidence import Claim

KINDS = ("taxonomy_labels", "relevance_adjudication")
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "fine_tuning_data"


class FineTuningError(ValueError):
    pass


async def _build_rows(session: AsyncSession, kind: str, *, label: str | None, limit: int) -> list[dict]:
    if kind == "taxonomy_labels":
        stmt = select(ArticleLabel).where(ArticleLabel.method.in_(("rule", "human"))).limit(limit)
        if label:
            stmt = stmt.where(ArticleLabel.label == label)
        rows = (await session.execute(stmt)).scalars().all()
        return [{"article_id": str(r.article_id), "label": r.label, "method": r.method,
                 "confidence": r.confidence, "taxonomy_version": r.taxonomy_version} for r in rows]
    if kind == "relevance_adjudication":
        stmt = select(Claim).where(Claim.verification_status.in_(("verified", "rejected"))).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()
        return [{"claim_key": r.claim_key, "claim_text": r.claim_text, "verification_status": r.verification_status,
                 "evidence": r.evidence_json} for r in rows]
    raise FineTuningError(f"kind must be one of {KINDS}")


async def build_dataset(
    session: AsyncSession, *, name: str, kind: str, label: str | None = None, limit: int = 5000, actor: str | None = None
) -> FineTuneDataset:
    if kind not in KINDS:
        raise FineTuningError(f"kind must be one of {KINDS}")
    if await session.scalar(select(FineTuneDataset.id).where(FineTuneDataset.name == name)):
        raise FineTuningError("a dataset with this name exists")
    rows = await _build_rows(session, kind, label=label, limit=limit)
    if not rows:
        raise FineTuningError("no reviewed stored data matches this selection yet; nothing to export")
    body = "\n".join(json.dumps(r, sort_keys=True, default=str) for r in rows)
    digest = hashlib.sha256(body.encode()).hexdigest()[:16]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / f"{name}.jsonl").write_text(body, encoding="utf-8")
    dataset = FineTuneDataset(
        name=name, kind=kind, source_query={"label": label, "limit": limit}, item_count=len(rows),
        digest=digest, format="jsonl", created_by=actor,
    )
    session.add(dataset)
    await session.commit()
    return dataset


async def list_datasets(session: AsyncSession) -> list[FineTuneDataset]:
    return (await session.execute(select(FineTuneDataset).order_by(FineTuneDataset.created_at.desc()))).scalars().all()


def fine_tuning_capability() -> dict:
    """Honest capability status, matching the pattern of engine.capabilities for embeddings/LLM."""
    return {"status": "not_configured", "provider": None,
            "detail": "No fine-tuning provider is configured; datasets can be built and versioned, but no job can run."}


async def create_job(
    session: AsyncSession, *, dataset_id, base_model: str, config: dict, actor: str | None = None
) -> FineTuneJob:
    dataset = await session.get(FineTuneDataset, dataset_id)
    if dataset is None:
        raise FineTuningError("dataset not found")
    cap = fine_tuning_capability()
    job = FineTuneJob(
        dataset_id=dataset_id, base_model=base_model, config=config, provider=cap["provider"],
        status="blocked" if cap["status"] == "not_configured" else "pending",
        status_reason=cap["detail"] if cap["status"] == "not_configured" else None,
        created_by=actor,
    )
    session.add(job)
    await session.commit()
    return job


async def list_jobs(session: AsyncSession) -> list[FineTuneJob]:
    return (await session.execute(select(FineTuneJob).order_by(FineTuneJob.created_at.desc()))).scalars().all()
