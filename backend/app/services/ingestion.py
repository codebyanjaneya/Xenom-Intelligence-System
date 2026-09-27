from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.adapters.ingestion import AdapterError, HtmlFixtureAdapter, RssFixtureAdapter, RssHttpAdapter
from app.models import Source
from app.schemas.ingestion import IngestionResult
from app.services.intelligence_pipeline import PipelineContext, process_documents

_FIXTURE_ROOT = (Path(__file__).resolve().parents[1] / "adapters" / "ingestion" / "fixtures").resolve()


class UnsupportedFixtureSourceError(ValueError):
    """Raised when a source is not explicitly configured for M4 fixture ingestion."""


class ArticlePersistenceError(RuntimeError):
    """Raised when article persistence fails for a reason other than deduplication."""


async def ingest_source(session: AsyncSession, source: Source) -> IngestionResult:
    """Admin/API ingestion of one configured RSS feed or fixture.

    Runs the same stage chain as every other path (structural checks, relevance,
    quality gate, dedupe). Network media enrichment stays off here so the endpoint
    is deterministic; the registry runner and scheduler enable it.
    """
    if source.source_type not in {"RSS", "HTML"}:
        raise UnsupportedFixtureSourceError(
            "Source is not configured for a supported RSS or HTML adapter"
        )

    retrieved_at = datetime.now(timezone.utc)
    if source.source_type == "RSS" and source.feed_url:
        adapter_name = "rss_http"
        documents = await RssHttpAdapter().fetch(
            source.feed_url,
            source_id=source.id,
            retrieved_at=retrieved_at,
        )
    else:
        if not source.fixture_path:
            raise UnsupportedFixtureSourceError(
                "Source is not configured with a public RSS feed or fixture"
            )
        if get_settings().environment.strip().lower() == "production":
            raise UnsupportedFixtureSourceError("Fixture ingestion is disabled in production")
        fixture_path = (_FIXTURE_ROOT / source.fixture_path).resolve()
        if _FIXTURE_ROOT not in fixture_path.parents or not fixture_path.is_file():
            raise UnsupportedFixtureSourceError("Configured fixture is not available")
        adapter_name = source.source_type.lower() + "_fixture"
        adapter = RssFixtureAdapter() if source.source_type == "RSS" else HtmlFixtureAdapter()
        documents = adapter.parse_file(
            fixture_path,
            source_id=source.id,
            source_url=source.canonical_url,
            retrieved_at=retrieved_at,
        )

    result = await process_documents(
        session,
        source,
        documents,
        ctx=PipelineContext(require_date=False, resolve_media=False),
    )
    if result.errors and result.rows_created == 0 and result.documents_deduplicated == 0 and result.documents_invalid == 0:
        raise ArticlePersistenceError("; ".join(result.errors[:3]))
    return IngestionResult(
        source_id=source.id,
        adapter=adapter_name,
        documents_found=len(documents),
        articles_created=result.rows_created,
        articles_deduplicated=result.documents_deduplicated,
    )
