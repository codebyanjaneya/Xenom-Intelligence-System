"""Embeddings as a semantic second opinion (PDF s.7, s.8, s.10). The provider is an open decision (s.16), so it is a clean
interface selected by configuration; with none configured every consumer falls back to lexical behaviour and the
capability status says so.

Providers
  none               (default) nothing leaves the environment; semantic features report ``not_configured``
  local              sentence-transformers model loaded in-process (no client content leaves the environment)
  openai_compatible  any /embeddings HTTP endpoint (explicit opt-in: client text is sent to that service)

Every call is logged in ``ai_usage`` (provider, model, operation, latency, status). Vectors are stored per chunk together with
the model name, so they can be re-embedded when the model changes.
"""
from __future__ import annotations

import logging
import math
import time
from typing import Protocol

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.models.intelligence import AiUsage
from app.services.markup import strip_markup

logger = logging.getLogger(__name__)

BATCH = 32
MAX_CHARS = 2000


class EmbeddingUnavailable(RuntimeError):
    """No provider is configured, or it failed. Callers fall back to lexical behaviour."""


class EmbeddingProvider(Protocol):
    name: str
    model: str

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAICompatibleEmbeddings:
    name = "openai_compatible"

    def __init__(self, base_url: str, model: str, api_key: str | None, timeout: float = 60.0, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url, self.model, self.api_key, self.timeout, self.transport = base_url.rstrip("/"), model, api_key, timeout, transport

    async def embed(self, texts: list[str]) -> list[list[float]]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as http:
            response = await http.post(f"{self.base_url}/embeddings", json={"model": self.model, "input": texts}, headers=headers)
        if response.status_code != 200:
            raise EmbeddingUnavailable(f"embedding endpoint returned HTTP {response.status_code}")
        try:
            data = sorted(response.json()["data"], key=lambda d: d["index"])
            vectors = [[float(x) for x in d["embedding"]] for d in data]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingUnavailable("embedding endpoint returned an unexpected payload") from exc
        if len(vectors) != len(texts):
            raise EmbeddingUnavailable("embedding endpoint returned a different number of vectors")
        return vectors


class LocalEmbeddings:
    name = "local"

    def __init__(self, model: str):
        self.model = model
        self._encoder = None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import asyncio

        def run() -> list[list[float]]:
            if self._encoder is None:
                try:
                    from sentence_transformers import SentenceTransformer
                except ImportError as exc:
                    raise EmbeddingUnavailable("sentence-transformers is not installed") from exc
                self._encoder = SentenceTransformer(self.model)
            return [[float(x) for x in v] for v in self._encoder.encode(texts, normalize_embeddings=True)]
        try:
            return await asyncio.to_thread(run)
        except EmbeddingUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - model download/load errors
            raise EmbeddingUnavailable(f"local embedding model failed: {type(exc).__name__}: {str(exc)[:120]}") from exc


_override: EmbeddingProvider | None = None
_local_cache: dict[str, LocalEmbeddings] = {}  # the loaded model is kept for the life of the process


def set_provider(provider: EmbeddingProvider | None) -> None:
    """Dependency injection point (tests and embedding of a different model)."""
    global _override
    _override = provider


def get_provider() -> EmbeddingProvider | None:
    if _override is not None:
        return _override
    s = get_settings()
    kind = s.embedding_provider.strip().lower()
    if kind == "local" and s.embedding_model:
        return _local_cache.setdefault(s.embedding_model, LocalEmbeddings(s.embedding_model))
    if kind == "openai_compatible" and s.embedding_model and s.embedding_base_url:
        return OpenAICompatibleEmbeddings(s.embedding_base_url, s.embedding_model, s.embedding_api_key)
    return None


def capability() -> dict:
    provider = get_provider()
    if provider is None:
        return {"status": "not_configured", "provider": None, "model": None,
                "detail": "No embedding provider is configured; semantic and hybrid search fall back to lexical."}
    return {"status": "configured", "provider": provider.name, "model": provider.model, "detail": None}


def prepare(value: str) -> str:
    return " ".join((strip_markup(value) or "").split())[:MAX_CHARS]


def vector_literal(vector: list[float]) -> str:
    if not vector or any(not math.isfinite(x) for x in vector):
        raise EmbeddingUnavailable("provider returned an empty or non-finite vector")
    return "[" + ",".join(f"{x:.8g}" for x in vector) + "]"


async def log_usage(session: AsyncSession, provider: EmbeddingProvider, *, operation: str, latency_ms: int, status: str, error: str | None = None,
                    article_id=None) -> None:
    session.add(AiUsage(provider=provider.name, model=provider.model, operation=operation, latency_ms=latency_ms, status=status,
                        error=(error or None) and error[:300], related_article_id=article_id))


async def embed_texts(session: AsyncSession, texts: list[str], *, operation: str) -> tuple[list[list[float]], EmbeddingProvider]:
    provider = get_provider()
    if provider is None:
        raise EmbeddingUnavailable("no embedding provider configured")
    started = time.time()
    try:
        vectors = await provider.embed([prepare(t) for t in texts])
    except EmbeddingUnavailable as exc:
        await log_usage(session, provider, operation=operation, latency_ms=int((time.time() - started) * 1000), status="error", error=str(exc))
        raise
    except Exception as exc:  # noqa: BLE001 - provider bugs and network errors are one failure mode for callers
        await log_usage(session, provider, operation=operation, latency_ms=int((time.time() - started) * 1000), status="error",
                        error=f"{type(exc).__name__}: {str(exc)[:200]}")
        raise EmbeddingUnavailable(f"embedding provider failed: {type(exc).__name__}") from exc
    await log_usage(session, provider, operation=operation, latency_ms=int((time.time() - started) * 1000), status="ok")
    return vectors, provider


async def embed_pending(session: AsyncSession, *, limit: int = 500, reembed_other_models: bool = True) -> dict:
    """Embed chunks of accepted articles that have no vector (or one from a different model). Admin/scheduler invoked."""
    provider = get_provider()
    if provider is None:
        return {"status": "not_configured", "embedded": 0, "detail": capability()["detail"]}
    condition = "(c.embedding is null" + (" or c.embedding_model is distinct from :model" if reembed_other_models else "") + ")"
    rows = (await session.execute(text(
        f"select c.id, c.text from document_chunks c join articles a on a.id = c.article_id "
        f"where a.processing_status = 'accepted' and a.quality_gate_passed and {condition} order by c.created_at, c.id limit :limit"),
        {"model": provider.model, "limit": limit})).all()
    embedded = 0
    try:
        for start in range(0, len(rows), BATCH):
            batch = rows[start:start + BATCH]
            vectors, _p = await embed_texts(session, [r[1] for r in batch], operation="embedding:chunks")
            for (chunk_id, _t), vector in zip(batch, vectors):
                await session.execute(text("update document_chunks set embedding = cast(:v as vector), embedding_model = :m, embedded_at = now() where id = :i"),
                                      {"v": vector_literal(vector), "m": provider.model, "i": chunk_id})
                embedded += 1
            await session.commit()
    except EmbeddingUnavailable as exc:
        await session.commit()  # keep the failure log and everything embedded so far
        return {"status": "error", "embedded": embedded, "pending": len(rows) - embedded, "detail": str(exc)}
    return {"status": "ok", "embedded": embedded, "pending": 0, "model": provider.model}
