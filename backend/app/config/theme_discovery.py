"""Discovery configuration for AI / technology themes (configuration only; nothing here is evidence).

Why this exists: the technology view reports a theme as evidenced only when a stored, quality-gated article uses the
theme's terms. Client-centred queries rarely surface, say, "digital twin" coverage, so each unevidenced theme gets its
own discovery query, anchored to each client so the client-relevance gate (the first, rule-based filter) still applies:
    "<client anchor>" <theme phrase>   ->  Google News RSS  ->  clean / dedupe / entity gate / quality gate  ->  DB
    ->  technology view (literal match + verbatim evidence).
Articles that do not mention the client are rejected by the existing gate, exactly as for every other query, so this
never manufactures evidence: a theme stays NO_EVIDENCE_IN_STORED_CORPUS until real articles pass the gate.
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus

from app.config.clients import CLIENT_CONFIG, get_all_clients

# theme key (config/intelligence_themes.py) -> discovery phrase. Edit here to add or retune a theme; no code changes.
THEME_DISCOVERY: dict[str, str] = {
    "multi_agent_ai": '"multi-agent AI"',
    "embodied_ai": '"embodied AI"',
    "digital_twins": '"digital twin"',
    "multimodal_ai": '"multimodal AI"',
    "emotion_intent": '"emotion recognition" AI',
    "trustworthy_ai": '"trustworthy AI"',
    "hallucination": '"AI hallucination"',
    "humanoids": '"humanoid robot"',
    "ai_models": '"Hugging Face"',
    "blockchain": "blockchain",
}


def client_anchor(client: str) -> str:
    """The client's primary quoted search phrase (first configured Google query), else its canonical name."""
    for q in CLIENT_CONFIG[client].google_news_queries:
        m = re.match(r'\s*("[^"]+")', q)
        if m:
            return m.group(1)
    return f'"{CLIENT_CONFIG[client].canonical_name}"'


def theme_specs(client: str | None = None, theme: str | None = None) -> list[tuple[str, str, str, str]]:
    """(client, theme_key, query, google_search_url) for every configured theme x client pair."""
    out = []
    for name in get_all_clients():
        if client and name != client:
            continue
        for key, phrase in THEME_DISCOVERY.items():
            if theme and key != theme:
                continue
            query = f"{client_anchor(name)} {phrase}"
            out.append((name, key, query, f"https://news.google.com/search?q={quote_plus(query)}"))
    return out
