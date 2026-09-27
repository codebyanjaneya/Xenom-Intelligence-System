"""Registry-driven ingestion runner.

    python -m app.services.registry_ingestion [--client JCB] [--key vips-notices]
        [--google] [--no-media] [--report report.json]

For every enabled source in ``source_registry`` (plus, optionally, the Google News
queries from ``clients.py``) it: fetches -> runs the shared pipeline core -> reports
per-stage counts. Reference-only sources are registered in ``sources`` (inactive) but
never fetched.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from urllib.parse import quote_plus

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.ingestion.base import AdapterError, NormalizedDocument
from app.adapters.ingestion.google_news import GoogleNewsAdapter
from app.adapters.ingestion.research import ArxivAdapter, CrossrefAdapter, HuggingFaceAdapter, OpenAlexAdapter, SemanticScholarAdapter
from app.adapters.ingestion.rss import RssHttpAdapter
from app.adapters.ingestion.site_adapters import (
    ArticlePageAdapter,
    DefinedgeBlogAdapter,
    LinkListingAdapter,
    TableRowsAdapter,
)
from app.config.clients import CLIENT_CONFIG, ENTITY_KEY_BY_CLIENT
from app.config.source_registry import SOURCE_REGISTRY, SourceSpec
from app.models import Source, SourceHealth
from app.models.architecture import ClientSource
from app.services.intelligence_pipeline import IntelligencePipelineResult, PipelineContext, process_documents
from app.services.intelligence_scoring import QUALITY_GATE_DEFAULT

logger = logging.getLogger(__name__)

__all__ = ["get_or_create_source", "sync_registry_sources", "run_registry", "run_spec", "run_google_query",
           "google_specs", "record_health", "spec_from_source", "run_source_row", "dry_run_source", "run_configured_sources"]

_FETCH_TIMEOUT = 240
_LAST_RUN: dict[str, float] = {}


async def get_or_create_source(
    session: AsyncSession, *, name: str, url: str, source_type: str, tier: str,
    feed_url: str | None = None, is_active: bool = True,
) -> Source:
    source = await session.scalar(select(Source).where(Source.canonical_url == url))
    if source is None:
        source = Source(name=name, canonical_url=url, feed_url=feed_url, source_type=source_type, tier=tier, is_active=is_active)
        session.add(source)
        await session.flush()
    elif not (source.config or {}).get("managed"):
        # Code-registry values apply until a manager edits the source; after that the stored row is authoritative.
        source.name, source.tier, source.is_active = name, tier, is_active
        source.source_type = source_type
        source.feed_url = feed_url or source.feed_url
    return source


async def sync_registry_sources(session: AsyncSession) -> dict[str, Source]:
    """Ensure a ``sources`` row exists for every registered source (reference-only ones inactive)."""
    rows: dict[str, Source] = {}
    for spec in SOURCE_REGISTRY:
        rows[spec.key] = await get_or_create_source(
            session, name=spec.name, url=spec.url, source_type=spec.db_source_type, tier=spec.tier,
            feed_url=spec.feed_url, is_active=spec.enabled,
        )
    await session.commit()
    return rows


def google_specs(client: str | None = None) -> list[tuple[str, str, str]]:
    """(client_name, query, canonical_url) for every Google News discovery query."""
    out = []
    for client_name, cfg in CLIENT_CONFIG.items():
        if client and ENTITY_KEY_BY_CLIENT[client_name] != client and client_name != client:
            continue
        for query in cfg.google_news_queries:
            out.append((client_name, query, f"https://news.google.com/search?q={quote_plus(query)}"))
    return out


async def _fetch(spec: SourceSpec, source: Source, retrieved_at: datetime) -> tuple[list[NormalizedDocument], list[str]]:
    errors: list[str] = []
    if spec.fetch_mode == "rss":
        docs = await RssHttpAdapter().fetch(spec.feed_url or spec.url, source_id=source.id, retrieved_at=retrieved_at)
    elif spec.fetch_mode == "seed_article":
        adapter = ArticlePageAdapter()
        docs = await adapter.fetch([spec.url], source_id=source.id, retrieved_at=retrieved_at)
        errors = adapter.errors
    elif spec.fetch_mode == "link_listing":
        adapter = LinkListingAdapter(link_pattern=spec.link_pattern or r"articleshow|/news/|/article/", render=spec.render)
        docs = await adapter.fetch(spec.url, source_id=source.id, retrieved_at=retrieved_at)
        errors = adapter.errors
    elif spec.fetch_mode == "table_rows":
        adapter = TableRowsAdapter(render=spec.render)
        docs = await adapter.fetch(spec.url, source_id=source.id, retrieved_at=retrieved_at)
        errors = adapter.errors
    elif spec.fetch_mode == "definedge_blog_api":
        adapter = DefinedgeBlogAdapter()
        docs = await adapter.fetch(source_id=source.id, retrieved_at=retrieved_at)
        errors = adapter.errors
    elif spec.fetch_mode in ("arxiv_api", "huggingface_hub", "openalex_api", "semantic_scholar_api", "crossref_api"):
        if not spec.query:
            raise AdapterError(f"{spec.fetch_mode} needs a search query in the source configuration")
        adapter = {"arxiv_api": ArxivAdapter, "huggingface_hub": HuggingFaceAdapter,
               "openalex_api": OpenAlexAdapter, "semantic_scholar_api": SemanticScholarAdapter,
               "crossref_api": CrossrefAdapter}[spec.fetch_mode]()
        docs = await adapter.fetch(spec.query, source_id=source.id, retrieved_at=retrieved_at, max_results=spec.max_results)
        errors = adapter.errors
    else:
        raise AdapterError(f"unsupported fetch mode {spec.fetch_mode}")
    return docs, errors


async def record_health(session: AsyncSession, source_id, *, seconds: float, error: str | None,
                        timeout: bool = False) -> None:
    """One source_health row per fetch attempt (ok / error / timeout). Health is measured, never assumed."""
    session.add(SourceHealth(
        source_id=source_id, checked_at=datetime.now(timezone.utc),
        status="timeout" if timeout else ("error" if error else "ok"),
        latency_ms=int(seconds * 1000), error_message=error[:500] if error else None,
    ))
    await session.commit()


def _report_base(key: str, name: str, client: str | None, mode: str, note: str = "") -> dict:
    return {"key": key, "name": name, "client": client, "mode": mode, "fetch_status": "pending", "fetch_error": None, "note": note}


async def _finish(report: dict, result: IntelligencePipelineResult, fetch_errors: list[str], started: float) -> dict:
    report.update(result.as_dict())
    report["fetch_status"] = "ok"
    report["adapter_errors"] = fetch_errors[:5]
    report["seconds"] = round(time.time() - started, 1)
    return report


async def run_spec(
    maker: async_sessionmaker[AsyncSession], spec: SourceSpec, *, gate: int = QUALITY_GATE_DEFAULT,
    resolve_media: bool = True, persist: bool = True,
) -> dict:
    """The ONE canonical ingestion path for a source: scheduler, manual run and the admin dry run all come through here.
    ``persist=False`` runs every stage (fetch, parse, clean, dedupe, relevance, gate) and rolls everything back."""
    report = _report_base(spec.key, spec.name, spec.client, spec.fetch_mode, spec.note)
    if not spec.enabled:
        report["fetch_status"] = "skipped:reference_only"
        return report
    started = time.time()
    async with maker() as session:
        source = await get_or_create_source(
            session, name=spec.name, url=spec.url, source_type=spec.db_source_type, tier=spec.tier,
            feed_url=spec.feed_url, is_active=True,
        )
        if (source.config or {}).get("managed") and not source.is_active:
            report["fetch_status"] = "skipped:disabled_by_manager"
            await session.rollback()
            return report
        if persist:
            await session.commit()
        try:
            docs, errors = await asyncio.wait_for(_fetch(spec, source, datetime.now(timezone.utc)), _FETCH_TIMEOUT)
        except (AdapterError, asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001 - reported below, never silent
            report["fetch_status"] = "failed"
            report["fetch_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            report["seconds"] = round(time.time() - started, 1)
            logger.error("Fetch failed for %s: %s", spec.key, report["fetch_error"])
            if persist:
                await record_health(session, source.id, seconds=time.time() - started, error=report["fetch_error"],
                                    timeout=isinstance(exc, asyncio.TimeoutError))
            else:
                await session.rollback()
            return report
        if persist:
            await record_health(session, source.id, seconds=time.time() - started, error=None)
        result = IntelligencePipelineResult()
        result.documents_fetched = len(docs)
        ctx = PipelineContext(
            client_key=spec.client, source_kind=spec.source_kind, require_date=spec.require_date,
            resolve_media=resolve_media, sample_limit=6 if persist else 40,
        )
        await process_documents(session, source, docs, quality_gate_threshold=gate, ctx=ctx, result=result, persist=persist)
        if not persist:
            await session.rollback()  # dry run: nothing of the above is stored
    report = await _finish(report, result, errors, started)
    if not persist:
        report["dry_run"] = True
    return report


def spec_from_source(source: Source, client_key: str | None = None) -> SourceSpec:
    """A runnable SourceSpec from a manager-defined ``sources`` row (its ``config`` says how to fetch it)."""
    cfg = source.config or {}
    if not cfg.get("fetch_mode"):
        raise AdapterError("source has no fetch configuration (config.fetch_mode)")
    return SourceSpec(
        key=f"db:{source.id}", client=client_key, name=source.name, url=source.canonical_url, fetch_mode=cfg["fetch_mode"],
        db_source_type=source.source_type or "OTHER_PUBLIC", tier=source.tier, source_kind=cfg.get("source_kind"),
        feed_url=source.feed_url, require_date=cfg.get("require_date", True), render=cfg.get("render", False),
        link_pattern=cfg.get("link_pattern"), query=cfg.get("query"), max_results=int(cfg.get("max_results", 25)),
    )


async def _client_keys_for(session: AsyncSession, source_id) -> list[str]:
    links = (await session.scalars(select(ClientSource).where(ClientSource.source_id == source_id, ClientSource.is_active.is_(True))
                                  .order_by(ClientSource.priority.desc(), ClientSource.client_key))).all()
    return [ENTITY_KEY_BY_CLIENT[name] for link in links
            for name in CLIENT_CONFIG if name.lower() == link.client_key.lower()]


async def run_source_row(maker: async_sessionmaker[AsyncSession], source_id, *, gate: int = QUALITY_GATE_DEFAULT,
                         resolve_media: bool = True, persist: bool = True) -> dict:
    async with maker() as session:
        source = await session.get(Source, source_id)
        if source is None:
            raise LookupError("source not found")
        client_keys = await _client_keys_for(session, source.id)
        specs = [spec_from_source(source, client_key) for client_key in client_keys] or [spec_from_source(source)]
    reports = [await run_spec(maker, spec, gate=gate, resolve_media=resolve_media, persist=persist) for spec in specs]
    if len(reports) == 1:
        return reports[0]
    merged = dict(reports[0])
    for report in reports[1:]:
        for key, value in report.items():
            if isinstance(value, int):
                merged[key] = merged.get(key, 0) + value
            elif key == "failed_sources":
                merged.setdefault(key, []).extend(value or [])
    merged["client"] = None
    return merged


async def dry_run_source(maker: async_sessionmaker[AsyncSession], spec: SourceSpec, *, gate: int = QUALITY_GATE_DEFAULT) -> dict:
    """Fetch and parse a source and show what the relevance engine would accept or reject. Stores nothing."""
    return await run_spec(maker, spec, gate=gate, resolve_media=False, persist=False)


async def run_configured_sources(maker: async_sessionmaker[AsyncSession], *, gate: int = QUALITY_GATE_DEFAULT,
                                 resolve_media: bool = True, only_due_minutes: int | None = None) -> list[dict]:
    """Every ACTIVE manager-defined source (``sources.config.fetch_mode``) that is not one of the code-registry URLs.
    One failing source never stops the others."""
    registry_urls = {s.url for s in SOURCE_REGISTRY}
    async with maker() as session:
        rows = (await session.scalars(select(Source).where(Source.is_active.is_(True)).order_by(Source.priority.desc(), Source.name))).all()
        specs = []
        for row in rows:
            if row.canonical_url in registry_urls or not (row.config or {}).get("fetch_mode"):
                continue
            client_keys = await _client_keys_for(session, row.id)
            specs.extend(spec_from_source(row, client_key) for client_key in client_keys) if client_keys else specs.append(spec_from_source(row))
    reports = []
    for spec in specs:
        if only_due_minutes is not None and time.time() - _LAST_RUN.get(spec.key, 0) < only_due_minutes * 60:
            continue
        try:
            reports.append(await run_spec(maker, spec, gate=gate, resolve_media=resolve_media))
        except Exception as exc:  # noqa: BLE001 - isolated per source
            reports.append({**_report_base(spec.key, spec.name, spec.client, spec.fetch_mode), "fetch_status": "failed",
                            "fetch_error": f"{type(exc).__name__}: {str(exc)[:200]}"})
        _LAST_RUN[spec.key] = time.time()
    return reports


async def run_google_query(
    maker: async_sessionmaker[AsyncSession], client_name: str, query: str, url: str, *,
    gate: int = QUALITY_GATE_DEFAULT, resolve_media: bool = True, when: str = "7d",
) -> dict:
    report = _report_base(f"google:{client_name}:{query}", f"Google News: {client_name} ({query})", ENTITY_KEY_BY_CLIENT[client_name], "google_news")
    started = time.time()
    async with maker() as session:
        source = await get_or_create_source(
            session, name=f"Google News: {client_name} ({query})", url=url, source_type="RSS", tier="TIER_2",
        )
        await session.commit()
        try:
            docs = await GoogleNewsAdapter().fetch(query=query, source_id=str(source.id), retrieved_at=datetime.now(timezone.utc), when=when)
        except Exception as exc:  # noqa: BLE001
            report["fetch_status"] = "failed"
            report["fetch_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            await record_health(session, source.id, seconds=time.time() - started, error=report["fetch_error"],
                                timeout=isinstance(exc, asyncio.TimeoutError))
            return report
        await record_health(session, source.id, seconds=time.time() - started, error=None)
        result = IntelligencePipelineResult()
        result.documents_fetched = len(docs)
        ctx = PipelineContext(client_key=ENTITY_KEY_BY_CLIENT[client_name], resolve_media=resolve_media)
        await process_documents(session, source, docs, quality_gate_threshold=gate, ctx=ctx, result=result)
    return await _finish(report, result, [], started)


async def run_registry(
    maker: async_sessionmaker[AsyncSession], *, client: str | None = None, keys: set[str] | None = None,
    include_google: bool = False, resolve_media: bool = True, gate: int = QUALITY_GATE_DEFAULT,
    only_due_minutes: int | None = None, google_when: str = "7d",
) -> list[dict]:
    """Run every selected registry source (and optionally Google queries); one report per source."""
    async with maker() as session:
        await sync_registry_sources(session)

    reports: list[dict] = []
    for spec in SOURCE_REGISTRY:
        if client and spec.client != client:
            continue
        if keys and spec.key not in keys:
            continue
        if only_due_minutes is not None and spec.enabled:
            if time.time() - _LAST_RUN.get(spec.key, 0) < only_due_minutes * 60:
                continue
        reports.append(await run_spec(maker, spec, gate=gate, resolve_media=resolve_media))
        if spec.enabled:
            _LAST_RUN[spec.key] = time.time()
    if not keys and not client:
        reports.extend(await run_configured_sources(maker, gate=gate, resolve_media=resolve_media, only_due_minutes=only_due_minutes))
    if include_google:
        for client_name, query, url in google_specs(client):
            reports.append(await run_google_query(maker, client_name, query, url, gate=gate, resolve_media=resolve_media, when=google_when))
    return reports


def _print(reports: list[dict]) -> None:
    cols = ["fetched", "documents_parsed", "documents_invalid", "documents_cleaned", "documents_entity_candidates",
            "documents_tagged_entity", "documents_quality_passed", "documents_deduplicated", "documents_cross_deduplicated",
            "documents_accepted", "documents_rejected"]
    print(f"{'SOURCE':44} {'FETCH':22} " + " ".join(c.replace('documents_', '')[:9].rjust(9) for c in cols))
    for r in reports:
        vals = [r.get("documents_fetched", 0) if c == "fetched" else r.get(c, 0) for c in cols]
        status = r["fetch_status"] if r["fetch_status"] != "failed" else "FAILED"
        print(f"{r['key'][:44]:44} {status[:22]:22} " + " ".join(str(v).rjust(9) for v in vals))
        if r.get("fetch_error"):
            print(f"    fetch error: {r['fetch_error']}")


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--client")
    parser.add_argument("--key", action="append")
    parser.add_argument("--google", action="store_true")
    parser.add_argument("--when", default="7d", help="Google News window, e.g. 7d for scheduled runs, 365d for a backfill")
    parser.add_argument("--no-media", action="store_true")
    parser.add_argument("--report")
    args = parser.parse_args()

    from app.db.session import dispose_engine, get_sessionmaker, init_engine

    init_engine()
    try:
        reports = await run_registry(
            get_sessionmaker(), client=args.client, keys=set(args.key) if args.key else None,
            include_google=args.google, resolve_media=not args.no_media, google_when=args.when,
        )
    finally:
        await dispose_engine()
    _print(reports)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(reports, fh, ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(_main())
