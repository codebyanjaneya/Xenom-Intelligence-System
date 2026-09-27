"""Validate health of all configured sources.

Checks HTTP reachability of each source's feed/canonical URL.
Stores results in source_health table.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.db.session import dispose_engine, get_sessionmaker
from app.models import Source
from app.services.source_health import validate_source_health


async def validate_all_sources() -> dict:
    """Validate health of all active sources. Returns summary stats."""
    maker = get_sessionmaker()
    async with maker() as session:
        # Get all active sources
        sources = await session.scalars(select(Source).where(Source.is_active == True))
        sources_list = list(sources)

        stats = {
            "total_checked": 0,
            "healthy": 0,
            "unhealthy": 0,
            "errors": 0,
            "results": {},
        }

        for source in sources_list:
            try:
                health = await validate_source_health(session, source)
                if health:
                    stats["total_checked"] += 1
                    stats["results"][source.name] = {
                        "status": health.status,
                        "latency_ms": health.latency_ms,
                        "error": health.error_message,
                    }
                    if health.status == "ok":
                        stats["healthy"] += 1
                    else:
                        stats["unhealthy"] += 1
            except Exception as e:
                stats["errors"] += 1
                stats["results"][source.name] = {
                    "status": "error",
                    "error": str(e),
                }

        await session.commit()
        return stats


async def main() -> None:
    """Run source validation and print results."""
    print("Validating all configured sources...")
    stats = await validate_all_sources()
    await dispose_engine()

    print(f"\nValidation Complete:")
    print(f"  Total checked: {stats['total_checked']}")
    print(f"  Healthy: {stats['healthy']}")
    print(f"  Unhealthy: {stats['unhealthy']}")
    print(f"  Errors: {stats['errors']}")

    if stats["results"]:
        print(f"\nDetailed Results:")
        for name, result in stats["results"].items():
            status_symbol = "[OK]" if result["status"] == "ok" else "[ERROR]"
            print(f"  {status_symbol} {name}: {result['status']}")
            if result.get("latency_ms"):
                print(f"      Latency: {result['latency_ms']}ms")
            if result.get("error"):
                print(f"      Error: {result['error']}")


if __name__ == "__main__":
    asyncio.run(main())
