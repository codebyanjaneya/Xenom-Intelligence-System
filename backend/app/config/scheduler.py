"""Background ingestion scheduler configuration.

Configures automatic pipeline execution every 10 minutes for all 6 client organizations.
Designed for production use with proper logging and error isolation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

__all__ = ["SCHEDULER_CONFIG"]


@dataclass
class SchedulerConfig:
    """Configuration for background ingestion scheduler."""

    enabled: bool = field(default_factory=lambda: os.environ.get("SCHEDULER_ENABLED", "true").strip().lower()
                          not in {"0", "false", "no", "off"})
    """Enable/disable the scheduler. Environment switch: SCHEDULER_ENABLED=false (default: enabled)."""

    interval_minutes: int = field(default_factory=lambda: max(1, int(os.environ.get("SCHEDULER_INTERVAL_MINUTES", "10"))))
    """Run the intelligence pipeline every N minutes (default: 10)."""

    timeout_seconds: int = 300
    """Maximum time for a single pipeline run (default: 5 min per client)."""

    retry_on_failure: bool = True
    """Retry failed sources next interval instead of skipping permanently."""

    log_run_summary: bool = True
    """Log detailed summary of each scheduled run."""

    theme_discovery_enabled: bool = True
    """Run the AI/technology theme discovery queries (config/theme_discovery.py)."""

    theme_discovery_interval_minutes: int = 60
    """Minimum minutes between theme discovery runs (60 client x theme queries; kept slower than client queries)."""

    registry_enabled: bool = True
    """Also run the source registry (official pages, feeds, seed articles) each cycle."""

    registry_interval_minutes: int = 60
    """Minimum minutes between runs of any single registry source (Google queries run every cycle)."""

    materialize_intelligence: bool = True
    """After each cycle persist the rule-derived insights (draft), opportunities and alerts for review (services/materialize.py)."""

    quality_gate_threshold: int = 40
    """Minimum relevance score for acceptance (0-100).

    40/100 accepts articles with direct client mentions (entity match +
    source quality + basic context). Lower threshold needed for Phase-1
    discovery when entity matcher only looks at ORG companies (not generic
    tech/industry keywords). Articles below 40 typically lack company mentions.
    """


# Global scheduler configuration (can be overridden via environment)
SCHEDULER_CONFIG = SchedulerConfig()
