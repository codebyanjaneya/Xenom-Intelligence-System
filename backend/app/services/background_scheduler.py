"""Background ingestion scheduler for continuous intelligence pipeline execution.

Runs the intelligence discovery pipeline every 10 minutes for all configured clients.
Logs results and handles failures gracefully.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.config.clients import get_all_clients, CLIENT_CONFIG, ENTITY_KEY_BY_CLIENT
from app.config.scheduler import SCHEDULER_CONFIG
from app.db.session import get_sessionmaker
from app.config.theme_discovery import theme_specs
from app.services import embeddings, materialize
from app.services.registry_ingestion import google_specs, run_google_query, run_registry

logger = logging.getLogger(__name__)

__all__ = ["SchedulerRunner", "run_scheduled_pipeline"]

_LAST_THEME_RUN: list[float] = []  # monotonic-ish wall time of the last theme discovery run (one element)


def _theme_discovery_due() -> bool:
    if not _LAST_THEME_RUN:
        return True
    return datetime.now(timezone.utc).timestamp() - _LAST_THEME_RUN[0] >= SCHEDULER_CONFIG.theme_discovery_interval_minutes * 60


def _public_summary(summary: dict | None) -> dict | None:
    """Scheduler summary as exposed by the API: counts as-is, error strings bounded (no stack traces, no paths)."""
    if summary is None:
        return None
    out = dict(summary)
    out["failed_sources"] = [str(x)[:200] for x in summary.get("failed_sources", [])[:20]]
    return out


class SchedulerRunner:
    """Manages background ingestion scheduling."""

    def __init__(self):
        self.running = False
        self.task = None
        self.last_started_at: str | None = None
        self.last_summary: dict | None = None
        self.next_run_at: str | None = None

    def status(self) -> dict:
        """In-memory scheduler state (what this process is doing), for the pipeline status endpoint."""
        return {
            "enabled": SCHEDULER_CONFIG.enabled, "running": self.running,
            "interval_minutes": SCHEDULER_CONFIG.interval_minutes, "last_started_at": self.last_started_at,
            "next_run_at": self.next_run_at if self.running else None, "last_summary": _public_summary(self.last_summary),
        }

    async def start(self) -> None:
        """Start the background scheduler."""
        if not SCHEDULER_CONFIG.enabled:
            logger.info("Scheduler is disabled (SCHEDULER_CONFIG.enabled=False)")
            return

        if self.running:
            logger.warning("Scheduler is already running")
            return

        self.running = True
        logger.info(f"Starting background scheduler (interval: {SCHEDULER_CONFIG.interval_minutes} min)")

        # Run immediately on startup, then repeat
        self.task = asyncio.create_task(self._scheduler_loop())

    async def _scheduler_loop(self) -> None:
        """Main scheduler loop."""
        while self.running:
            try:
                self.last_started_at = datetime.now(timezone.utc).isoformat()
                self.last_summary = await run_scheduled_pipeline()
            except Exception as e:
                logger.error(f"Scheduler run failed: {e}", exc_info=True)

            self.next_run_at = (datetime.now(timezone.utc) + timedelta(minutes=SCHEDULER_CONFIG.interval_minutes)).isoformat()
            # Wait for the next interval
            await asyncio.sleep(SCHEDULER_CONFIG.interval_minutes * 60)

    async def stop(self) -> None:
        """Stop the background scheduler."""
        self.running = False
        self.next_run_at = None
        if self.task:
            self.task.cancel()
            self.task = None
        logger.info("Scheduler stopped")


async def run_scheduled_pipeline() -> dict:
    """Run one cycle only when this process acquires the shared DB lock."""
    maker = get_sessionmaker()
    engine = getattr(maker, "kw", {}).get("bind")
    if engine is None:
        return await _run_scheduled_pipeline()
    async with engine.connect() as lock_connection:
        acquired = await lock_connection.scalar(text("SELECT pg_try_advisory_lock(2147483641)"))
        if not acquired:
            return {"status": "skipped", "reason": "another scheduler instance owns the cycle lock"}
        try:
            return await _run_scheduled_pipeline()
        finally:
            await lock_connection.execute(text("SELECT pg_advisory_unlock(2147483641)"))


async def _fetch_query(sessionmaker, client_name: str, query: str, url: str):
    attempts = 2 if SCHEDULER_CONFIG.retry_on_failure else 1
    for attempt in range(attempts):
        try:
            return await asyncio.wait_for(
                run_google_query(sessionmaker, client_name, query, url,
                                 gate=SCHEDULER_CONFIG.quality_gate_threshold, resolve_media=True),
                SCHEDULER_CONFIG.timeout_seconds,
            )
        except asyncio.TimeoutError:
            if attempt + 1 >= attempts:
                raise
            logger.warning("Retrying scheduler query %s/%s", client_name, query)


async def _run_scheduled_pipeline() -> dict:
    """Execute one complete pipeline run for all configured clients.

    Fetches Google News RSS for all 6 clients, processes articles through
    the full pipeline (parse → clean → dedupe → tag → score → gate),
    and logs the run summary.

    Returns:
        Run summary with counts and any errors.
    """
    started_at = datetime.now(timezone.utc)
    clients = get_all_clients()
    sessionmaker = get_sessionmaker()

    summary = {
        "started_at": started_at.isoformat(),
        "clients_processed": len(clients),
        "total_fetched": 0,
        "total_parsed": 0,
        "total_deduplicated": 0,
        "total_accepted": 0,
        "total_rejected": 0,
        "failed_sources": [],
        "by_client": {},
    }

    # Every client, every configured Google News query, through the registry's tested path (which also records
    # source health). One failing query or client never stops the cycle.
    for client_name in clients:
        client_summary = {"queries": [], "total_fetched": 0, "total_accepted": 0, "total_rejected": 0}
        for _client, query, url in google_specs(client_name):
            try:
                report = await _fetch_query(sessionmaker, client_name, query, url)
            except asyncio.TimeoutError:
                logger.warning(f"Timeout processing {client_name}/{query}")
                summary["failed_sources"].append(f"{client_name}/{query}: timeout")
                continue
            except Exception as e:  # noqa: BLE001 - isolated per query
                logger.error(f"Error processing {client_name}/{query}: {e}")
                summary["failed_sources"].append(f"{client_name}/{query}: {str(e)}")
                continue
            if report.get("fetch_status") == "failed":
                summary["failed_sources"].append(f"{client_name}/{query}: {report.get('fetch_error')}")
                continue
            fetched = report.get("documents_fetched", 0)
            accepted = report.get("documents_accepted", 0)
            rejected = report.get("documents_rejected", 0)
            client_summary["queries"].append({"query": query, "fetched": fetched, "accepted": accepted, "rejected": rejected})
            client_summary["total_fetched"] += fetched
            client_summary["total_accepted"] += accepted
            client_summary["total_rejected"] += rejected
            summary["total_fetched"] += fetched
            summary["total_parsed"] += report.get("documents_parsed", 0)
            summary["total_deduplicated"] += report.get("documents_deduplicated", 0)
            summary["total_accepted"] += accepted
            summary["total_rejected"] += rejected
            if report.get("adapter_errors"):
                summary["failed_sources"].append(f"{client_name}/{query}: {report['adapter_errors'][0]}")
        summary["by_client"][client_name] = client_summary

    if SCHEDULER_CONFIG.theme_discovery_enabled and _theme_discovery_due():
        theme = {"queries": 0, "fetched": 0, "accepted": 0, "rejected": 0, "failed": 0}
        for client_name, theme_key, query, url in theme_specs():
            try:
                report = await _fetch_query(sessionmaker, client_name, query, url)
            except Exception as e:  # noqa: BLE001 - isolated per query
                theme["failed"] += 1
                summary["failed_sources"].append(f"theme:{theme_key}/{client_name}: {str(e) or type(e).__name__}")
                continue
            if report.get("fetch_status") == "failed":
                theme["failed"] += 1
                summary["failed_sources"].append(f"theme:{theme_key}/{client_name}: {report.get('fetch_error')}")
                continue
            theme["queries"] += 1
            theme["fetched"] += report.get("documents_fetched", 0)
            theme["accepted"] += report.get("documents_accepted", 0)
            theme["rejected"] += report.get("documents_rejected", 0)
        summary["total_fetched"] += theme["fetched"]
        summary["total_accepted"] += theme["accepted"]
        summary["total_rejected"] += theme["rejected"]
        summary["theme_discovery"] = theme
        _LAST_THEME_RUN[:] = [datetime.now(timezone.utc).timestamp()]

    if SCHEDULER_CONFIG.registry_enabled:
        try:
            registry_reports = await run_registry(
                sessionmaker, resolve_media=True, gate=SCHEDULER_CONFIG.quality_gate_threshold,
                only_due_minutes=SCHEDULER_CONFIG.registry_interval_minutes,
            )
            summary["registry_sources_run"] = len([r for r in registry_reports if r["fetch_status"] == "ok"])
            summary["total_accepted"] += sum(r.get("documents_accepted", 0) for r in registry_reports)
            summary["total_rejected"] += sum(r.get("documents_rejected", 0) for r in registry_reports)
            for r in registry_reports:
                if r["fetch_status"] == "failed":
                    summary["failed_sources"].append(f"{r['key']}: {r['fetch_error']}")
        except Exception as e:
            logger.error(f"Registry run failed: {e}", exc_info=True)
            summary["failed_sources"].append(f"registry: {e}")

    # Vectors for the chunks this cycle added (only when an embedding provider is configured; otherwise search stays lexical).
    if embeddings.get_provider() is not None:
        try:
            async with sessionmaker() as embed_session:
                summary["embeddings"] = await embeddings.embed_pending(embed_session, limit=1000)
            if summary["embeddings"].get("status") == "error":
                summary["failed_sources"].append(f"embeddings: {summary['embeddings'].get('detail')}")
        except Exception as e:  # noqa: BLE001 - never stops the cycle
            summary["failed_sources"].append(f"embeddings: {type(e).__name__}: {str(e)[:120]}")

    if SCHEDULER_CONFIG.materialize_intelligence:
        try:
            async with sessionmaker() as materialize_session:
                results = await materialize.materialize_all(materialize_session)
            summary["materialized"] = {r["client"]: {k: v for k, v in r.items() if k != "client" and v} for r in results}
            summary["failed_sources"].extend(f"materialize/{r['client']}: {r['error']}" for r in results if r.get("error"))
        except Exception as e:  # noqa: BLE001 - never stops the cycle
            summary["failed_sources"].append(f"materialize: {type(e).__name__}: {str(e)[:120]}")

    completed_at = datetime.now(timezone.utc)
    duration_sec = (completed_at - started_at).total_seconds()
    summary["completed_at"] = completed_at.isoformat()
    summary["duration_seconds"] = duration_sec

    # Log run summary
    if SCHEDULER_CONFIG.log_run_summary:
        logger.info(
            f"Scheduler run complete | "
            f"Clients: {summary['clients_processed']} | "
            f"Fetched: {summary['total_fetched']} | "
            f"Accepted: {summary['total_accepted']} | "
            f"Rejected: {summary['total_rejected']} | "
            f"Failed sources: {len(summary['failed_sources'])} | "
            f"Duration: {duration_sec:.1f}s"
        )

    return summary


# Global scheduler instance
_scheduler = None


def get_scheduler() -> SchedulerRunner:
    """Get or create the global scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = SchedulerRunner()
    return _scheduler
