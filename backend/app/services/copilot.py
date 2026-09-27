from __future__ import annotations

import json
import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.clients import CLIENT_CONFIG, get_all_clients
from app.schemas.copilot import CopilotAnswer, CopilotCitation, CopilotRequest, CopilotResponse
from app.services import llm
from app.services.client_engine import client_briefing, topic_briefing
from app.services.domain_intelligence import competitor_view, morning_digest, research_view
from app.services.researchers import list_researchers


class CopilotUnavailable(RuntimeError):
    pass


def _client_from_query(query: str) -> str | None:
    """Resolve canonical client names and configured aliases without guessing."""
    lowered = query.casefold()
    candidates = []
    for canonical, config in CLIENT_CONFIG.items():
        candidates.append((canonical, canonical))
        candidates.extend((canonical, alias) for alias in config.aliases)
    for canonical, label in sorted(candidates, key=lambda item: len(item[1]), reverse=True):
        if re.search(rf"(?<!\w){re.escape(label.casefold())}(?!\w)", lowered):
            return canonical
    return None


def _intent(query: str) -> tuple[str, str | None]:
    lowered = query.casefold()
    client = _client_from_query(query)
    if "competitor" in lowered:
        return "competitors", client
    if "researcher" in lowered or "scientist" in lowered or "author" in lowered:
        return "researchers", client
    if "research" in lowered or "paper" in lowered or "researcher" in lowered:
        return "research", client
    if "today" in lowered or "digest" in lowered or "morning" in lowered:
        return "digest", client
    for keyword, intent in (
        ("opportunit", "opportunities"),
        ("recommendation", "recommendations"),
        ("alert", "alerts"),
        ("insight", "insights"),
        ("news", "news"),
    ):
        if keyword in lowered:
            return intent, client
    if client:
        return "client_briefing", client
    return "search", None


def _conversation_context(request: CopilotRequest) -> list[dict[str, str]]:
    """Keep only a small client-supplied transcript; the database remains the source of truth."""
    return [{"query": turn.query, "answer": turn.answer, "intent": turn.intent, "client": turn.client or ""}
            for turn in request.history[-6:]]


def _history_client(history: list[dict[str, str]]) -> str | None:
    for turn in reversed(history):
        if turn.get("client"):
            return turn["client"]
    return None


def _platform_intent(query: str) -> str | None:
    lowered = query.casefold()
    if any(term in lowered for term in ("what is xenom", "what can xenom", "how does xenom", "platform overview", "system status")):
        return "platform"
    return None


def _unknown_company_query(query: str) -> bool:
    return bool(re.search(r"\b(?:company|client|organisation|organization)\s+(?:called|named)\s+[\w.-]+", query, re.I))


def _citations(data: object) -> list[CopilotCitation]:
    dumped = json.dumps(data, default=str)
    urls = re.findall(r"https?://[^\"\s]+", dumped)
    return [CopilotCitation(title="Source returned by Xenom Intel", url=url) for url in dict.fromkeys(urls[:12])]


def _validated_citations(citations: list[CopilotCitation], data: object, fallback: list[CopilotCitation]) -> list[CopilotCitation]:
    evidence = json.dumps(data, default=str)
    allowed_urls = set(re.findall(r"https?://[^\"\s]+", evidence))
    valid = [citation for citation in citations
             if citation.url in allowed_urls and (not citation.quote or citation.quote in evidence)]
    return valid or fallback


async def answer(session: AsyncSession, request: CopilotRequest) -> CopilotResponse:
    query = " ".join(request.query.split())
    history = _conversation_context(request)
    current_intent, current_client = _intent(query)
    unknown_company = _unknown_company_query(query) and current_client is None
    intent, client = current_intent, current_client
    if not unknown_company and not client and (current_intent != "search" or len(query.split()) <= 8):
        client = _history_client(history)
    if not current_client and client and current_intent == "search":
        intent = "client_briefing"
    platform_intent = _platform_intent(query)
    if platform_intent:
        intent, client = platform_intent, None

    if intent == "client_briefing" and client:
        data = (await client_briefing(session, client, limit=20)).model_dump(mode="json")
    elif intent == "competitors" and client:
        data = (await competitor_view(session, client)).model_dump(mode="json")
    elif intent == "researchers" and client:
        data = await list_researchers(session, q=client, limit=20)
    elif intent == "researchers":
        data = await list_researchers(session, q=None, limit=20)
    elif intent == "research" and client:
        data = (await topic_briefing(session, f"{client} research", limit=20)).model_dump(mode="json")
    elif intent == "research":
        data = (await research_view(session)).model_dump(mode="json")
    elif intent == "digest":
        data = (await morning_digest(session)).model_dump(mode="json")
    elif intent in {"insights", "opportunities", "recommendations", "alerts", "news"} and client:
        data = (await client_briefing(session, client, limit=20)).model_dump(mode="json")
    elif intent == "search" and not client and not unknown_company:
        data = (await topic_briefing(session, query, limit=20)).model_dump(mode="json")
    else:
        data = {}
    citations = _citations(data)
    if not citations:
        return CopilotResponse(query=query, intent=intent, client=client,
                               answer="Insufficient evidence in the stored corpus for this question.",
                               insufficient_evidence=True, citations=[], data={})
    context = json.dumps({
        "conversation": history,
        "current_query": query,
        "intent": intent,
        "client": client,
        "verified_engine_data": data,
        "verified_citations": [c.model_dump() for c in citations],
    }, default=str)[:18000]
    try:
        generated = await llm.run_structured(session, "copilot", context, CopilotAnswer)
        return CopilotResponse(query=query, intent=intent, client=client, answer=generated.answer, insufficient_evidence=generated.insufficient_evidence,
                       citations=_validated_citations(generated.citations, data, citations), data=data)
    except llm.LLMUnavailable:
        return CopilotResponse(query=query, intent=intent, client=client, answer="Verified engine data is available, but the configured language model is unavailable. Review the structured results and sources below.", insufficient_evidence=False, citations=citations, data=data)
    except llm.LLMOutputInvalid as exc:
        raise CopilotUnavailable(str(exc)) from exc