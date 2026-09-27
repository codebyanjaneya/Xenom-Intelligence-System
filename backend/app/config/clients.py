"""Client intelligence configuration.

Registry of the six monitored clients: descriptions and Google News discovery queries.
Matching/relevance vocabulary lives in ``entity_config.py`` (single source of truth);
the keyword fields exposed here are derived from it so the two can never drift.

Client identities (verified against the supplied source URLs):
    JCB        JCB India Ltd (construction equipment)
    ACE        Action Construction Equipment Ltd (cranes; NSE: ACE)
    Vikas      Vikas Group (automotive thermal management / emission / e-mobility)
    VIPS       Vivekananda Institute of Professional Studies - Technical Campus, Delhi
    ISM        India Semiconductor Mission (MeitY) -- NOT Institute for Supply Management
    Definedge  Definedge Securities (Pune wealth-tech / brokerage)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.config.entity_config import ENTITY_CONFIGS

__all__ = ["CLIENT_CONFIG", "ENTITY_KEY_BY_CLIENT", "get_client_config", "get_all_clients", "register_dynamic_client"]


@dataclass(frozen=True)
class ClientConfig:
    """Configuration for a single monitored client."""

    canonical_name: str
    aliases: list[str]
    keywords: list[str]
    technology_keywords: list[str]
    industry_keywords: list[str]
    google_news_queries: list[str]
    """Google News RSS search queries (quoted phrases keep them precise)."""
    trusted_sites: list[str] | None = None
    exclusion_keywords: list[str] | None = None
    description: str | None = None


# Client name (Entity.canonical_name) -> key in ENTITY_CONFIGS
ENTITY_KEY_BY_CLIENT: Final[dict[str, str]] = {
    "JCB": "JCB",
    "ACE": "ACE",
    "Definedge": "Definedge",
    "ISM": "ISM",
    "VIPS": "VIPS",
    "Vikas": "VIKAS",
}

_TECH_TERMS = {"ai", "artificial intelligence", "iot", "ev", "electric vehicle", "hydrogen", "wealth-tech", "fintech", "semiconductor", "osat", "fab"}


def _derived(client: str) -> dict:
    cfg = ENTITY_CONFIGS[ENTITY_KEY_BY_CLIENT[client]]
    tech = [k for k in cfg.context_keywords + cfg.industry_terms if k.lower() in _TECH_TERMS]
    return {
        "keywords": list(cfg.context_keywords),
        "technology_keywords": tech,
        "industry_keywords": list(cfg.industry_terms),
        "trusted_sites": list(cfg.trusted_sources),
        "exclusion_keywords": list(cfg.hard_negative_keywords) + list(cfg.negative_keywords),
    }


CLIENT_CONFIG: Final[dict[str, ClientConfig]] = {
    "JCB": ClientConfig(
        canonical_name="JCB",
        aliases=["JCB India", "JCB India Limited", "J.C. Bamford"],
        google_news_queries=[
            '"JCB India"',
            '"JCB India Limited"',
            '"J.C. Bamford"',
            "JCB backhoe loader India launch",
        ],
        description="JCB India Ltd, construction equipment manufacturer (Ballabgarh, Pune, Jaipur, Vadodara plants)",
        **_derived("JCB"),
    ),
    "ACE": ClientConfig(
        canonical_name="ACE",
        aliases=["Action Construction Equipment", "ACE Cranes"],
        google_news_queries=[
            '"Action Construction Equipment"',
            '"ACE Cranes"',
            '"ACE KATO"',
            "NSE ACE Action Construction shares",
        ],
        description="Action Construction Equipment Ltd (NSE: ACE), Indian crane and material-handling manufacturer",
        **_derived("ACE"),
    ),
    "Definedge": ClientConfig(
        canonical_name="Definedge",
        aliases=["Definedge Securities"],
        google_news_queries=[
            '"Definedge Securities"',
            "Definedge wealth-tech",
            '"Hemant Luthra" Definedge',
        ],
        description="Definedge Securities, Pune-based wealth-tech and brokerage firm",
        **_derived("Definedge"),
    ),
    "ISM": ClientConfig(
        canonical_name="ISM",
        aliases=["India Semiconductor Mission", "ISM 2.0", "Semicon India"],
        google_news_queries=[
            '"India Semiconductor Mission"',
            "Semicon India ISM semiconductor",
            "India semiconductor fab OSAT approval",
        ],
        description="India Semiconductor Mission (MeitY), the government programme for semiconductor manufacturing",
        **_derived("ISM"),
    ),
    "VIPS": ClientConfig(
        canonical_name="VIPS",
        aliases=["Vivekananda Institute of Professional Studies", "VIPS-TC"],
        google_news_queries=[
            '"Vivekananda Institute of Professional Studies"',
            '"VIPS-TC"',
            "VIPS Pitampura Delhi college",
        ],
        description="Vivekananda Institute of Professional Studies - Technical Campus, Pitampura, Delhi (GGSIPU affiliated)",
        **_derived("VIPS"),
    ),
    "Vikas": ClientConfig(
        canonical_name="Vikas",
        aliases=["Vikas Group", "Pranav Vikas", "Sanden Vikas"],
        google_news_queries=[
            '"Vikas Group" automotive',
            '"Pranav Vikas"',
            '"Sanden Vikas"',
            '"Vikas Group" bus HVAC EV',
        ],
        description="Vikas Group, automotive thermal-management, emission-control and e-mobility solutions provider",
        **_derived("Vikas"),
    ),
}


def get_client_config(canonical_name: str) -> ClientConfig | None:
    """Retrieve configuration for a client by canonical name (e.g. "JCB")."""
    return CLIENT_CONFIG.get(canonical_name)


def get_all_clients() -> list[str]:
    """Return list of all configured client canonical names."""
    return list(CLIENT_CONFIG.keys())


def register_dynamic_client(config: ClientConfig, entity_key: str | None = None) -> None:
    """Register a persisted company in the same runtime registries used by the engine."""
    key = entity_key or config.canonical_name
    CLIENT_CONFIG[config.canonical_name] = config
    ENTITY_KEY_BY_CLIENT[config.canonical_name] = key
