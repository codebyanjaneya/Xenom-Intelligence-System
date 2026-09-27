"""LLM enrichment (PDF s.10, s.13): the model enriches the pipeline, it never replaces it and never runs on every article.

Guarantees, enforced here and not left to the prompt:
* every call is logged in ``ai_usage`` (provider, model, operation, prompt version, tokens, latency, cost, status, error);
* prompts are versioned constants; the version is stored with every call so outputs can be reproduced;
* output is a structured schema; anything else is rejected and logged as ``invalid_output``;
* an extracted claim must carry a VERBATIM quote that exists in the stored source text, otherwise it is dropped;
* synthesis may use only retrieved, stored claims: every generated sentence must cite claim ids that were supplied, a
  sentence with no valid citation is removed, and with nothing left the answer is "insufficient evidence" (nothing stored);
* with no evidence to work from the model is not called at all;
* confidence is computed from the evidence (source tier, independent publishers), never taken from the model;
* generated text is stored as a labelled draft (``generated`` / status ``draft``) and reviewed before publishing.

With ``LLM_PROVIDER=none`` (default) every operation reports ``not_configured``. Nothing is simulated.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import taxonomy as taxonomy_cfg
from app.config.settings import get_settings
from app.models import Claim, ClaimSource, Insight, InsightClaim, ReviewItem
from app.models.architecture import ArticleLabel, Recommendation
from app.models.content import Article
from app.models.entities import Entity
from app.models.intelligence import AiUsage
from app.models.sources import Source

logger = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    """No provider configured, budget exhausted, or the provider failed."""


class LLMOutputInvalid(RuntimeError):
    """The model answered, but not with the required structure."""


@dataclass
class LLMResult:
    text: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class LLMProvider(Protocol):
    name: str
    model: str

    async def complete(self, system: str, user: str, *, max_tokens: int) -> LLMResult: ...


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str, api_key: str, base_url: str | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.model, self.api_key, self.base_url, self.transport = model, api_key, (base_url or "https://api.anthropic.com").rstrip("/"), transport

    async def complete(self, system: str, user: str, *, max_tokens: int) -> LLMResult:
        async with httpx.AsyncClient(timeout=90, transport=self.transport) as http:
            r = await http.post(f"{self.base_url}/v1/messages", headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
                                json={"model": self.model, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": user}]})
        if r.status_code != 200:
            raise LLMUnavailable(f"LLM endpoint returned HTTP {r.status_code}")
        try:
            body = r.json()
            return LLMResult("".join(b.get("text", "") for b in body["content"] if b.get("type") == "text"),
                             body.get("usage", {}).get("input_tokens"), body.get("usage", {}).get("output_tokens"))
        except (KeyError, TypeError, ValueError) as exc:
            raise LLMUnavailable("LLM endpoint returned an unexpected payload") from exc


class OpenAIChatProvider:
    name = "openai_compatible"

    def __init__(self, model: str, api_key: str | None, base_url: str, transport: httpx.AsyncBaseTransport | None = None):
        self.model, self.api_key, self.base_url, self.transport = model, api_key, base_url.rstrip("/"), transport

    async def complete(self, system: str, user: str, *, max_tokens: int) -> LLMResult:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        async with httpx.AsyncClient(timeout=90, transport=self.transport) as http:
            r = await http.post(f"{self.base_url}/chat/completions", headers=headers, json={
                "model": self.model, "max_tokens": max_tokens, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
        if r.status_code != 200:
            raise LLMUnavailable(f"LLM endpoint returned HTTP {r.status_code}")
        try:
            body = r.json()
            return LLMResult(body["choices"][0]["message"]["content"], body.get("usage", {}).get("prompt_tokens"), body.get("usage", {}).get("completion_tokens"))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMUnavailable("LLM endpoint returned an unexpected payload") from exc


_override: LLMProvider | None = None


def set_provider(provider: LLMProvider | None) -> None:
    global _override
    _override = provider


GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_MODEL = "openai/gpt-oss-120b"


def get_provider() -> LLMProvider | None:
    if _override is not None:
        return _override
    s = get_settings()
    kind = s.llm_provider.strip().lower()
    if kind == "anthropic" and s.llm_model and s.llm_api_key:
        return AnthropicProvider(s.llm_model, s.llm_api_key, s.llm_base_url)
    if kind == "openai_compatible" and s.llm_model and s.llm_base_url:
        return OpenAIChatProvider(s.llm_model, s.llm_api_key, s.llm_base_url)
    if kind == "groq" and s.groq_api_key:
        # Groq's endpoint is OpenAI-compatible: reuse OpenAIChatProvider rather than a separate client.
        provider = OpenAIChatProvider(s.groq_model or GROQ_DEFAULT_MODEL, s.groq_api_key, GROQ_BASE_URL)
        provider.name = "groq"
        return provider
    return None


def capability() -> dict:
    provider = get_provider()
    s = get_settings()
    if provider is None:
        return {"status": "not_configured", "provider": None, "model": None,
                "detail": "No LLM provider is configured; borderline adjudication, claim extraction, label gaps and synthesis are unavailable.",
                "operations": list(PROMPTS)}
    return {"status": "configured", "provider": provider.name, "model": provider.model, "daily_call_budget": s.llm_daily_call_budget,
            "operations": list(PROMPTS), "detail": None}


# ---- versioned prompts --------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Prompt:
    version: str
    system: str


_JSON_ONLY = " Answer with one JSON object and nothing else — no markdown, no code fences, no extra keys."
# Every prompt spells out the exact JSON field names/shape. Relying on a model to infer field names from prose
# alone is provider-specific (some are more compliant than others); an explicit shape is what makes the same
# prompt work reliably across providers (Anthropic, OpenAI-compatible, Groq, ...).
PROMPTS: dict[str, Prompt] = {
    "discover_company": Prompt("1", "Normalize a company onboarding request using only the supplied web search results. Never invent facts, domains, keywords or source URLs. "
                                'Return exactly: {"canonical_name":"...","official_domain":"...","description":"...","aliases":[],"industries":[],"keywords":[],"sources":[]} '
                                "official_domain must appear in a supplied result and sources must contain only supplied result URLs." + _JSON_ONLY),
    "copilot": Prompt("1", "Answer the user's question using only the supplied Xenom Intel data. Never invent facts, dates, clients, capabilities, competitors, opportunities, or sources. If evidence is insufficient, set insufficient_evidence=true and say so. Keep the answer concise and cite only the supplied source URLs in citations. Respond with exactly: {\"answer\":\"...\",\"insufficient_evidence\":true|false,\"citations\":[{\"title\":\"...\",\"url\":\"...\",\"date\":\"...\",\"quote\":\"...\"}]}" + _JSON_ONLY),
    "adjudicate_relevance": Prompt("2", "You judge whether a stored article is about a named client organisation. Use only the text provided. "
                                        "Quote one verbatim sentence from the article that supports your decision, or answer insufficient_evidence. "
                                        'Respond with exactly this JSON shape: {"decision": "relevant" | "not_relevant" | "insufficient_evidence", '
                                        '"reason": "<short reason, max 500 chars>", "quote": "<verbatim sentence from the article, or null>"}.' + _JSON_ONLY),
    "extract_claims": Prompt("2", "Extract factual claims stated in the article text. Each claim needs a verbatim quote copied exactly from the text. "
                                  "Do not infer, summarise beyond the text, or use outside knowledge. If there are no clear factual claims, return an empty list. "
                                  'Respond with exactly this JSON shape: {"claims": [{"claim": "<one factual statement>", '
                                  '"quote": "<verbatim quote from the text supporting it>", "claim_type": "<short category, or null>"}]} '
                                  "(at most 10 items)." + _JSON_ONLY),
    "classify_labels": Prompt("2", "Assign taxonomy labels to the article using only the allowed labels. Each label needs a verbatim supporting quote. "
                              'Respond with exactly this JSON shape: {"labels": [{"label": "<one allowed label>", '
                              '"quote": "<verbatim quote from the text supporting it>"}]} (at most 6 items).' + _JSON_ONLY),
    "synthesize": Prompt("2", "Write short synthesis sentences using ONLY the numbered claims provided. Every sentence must list the claim ids it relies on. "
                              "If the claims do not support a statement, do not write it; if nothing is supported set insufficient_evidence to true and sentences to []. "
                              'Respond with exactly this JSON shape: {"sentences": [{"text": "<one synthesis sentence>", "claim_refs": ["<claim id>", ...]}], '
                              '"insufficient_evidence": true | false} (at most 8 sentences).' + _JSON_ONLY),
    "fact_check": Prompt("2", "You verify a piece of news. You are given the claim/source (which may be a URL from anywhere, not only Xenom's "
                              "own registered sources), optionally a FETCHED SOURCE PAGE (that page's own domain/title/fetch status, fetched live — "
                              "note fetching it only proves the page exists, never that its claims are true), and a list of stored articles found "
                              "searching for it (title, source, tier, url only — no full text). "
                              "Judge ONLY from whether the listed stored articles corroborate the claim, and from clear internal red flags in the "
                              "claim/fetched text itself (e.g. impossible dates, self-contradiction). Never use outside knowledge you were trained on "
                              "to declare something true or false, and never treat a successful fetch alone as proof the claim is real. "
                              "If one or more credible stored articles (source/title) plainly cover the same claim, verdict=LIKELY_REAL. "
                              "If there is a clear internal red flag, verdict=LIKELY_FAKE and say exactly what the red flag is. "
                              "If there is neither corroboration nor a clear red flag, verdict=UNVERIFIED — say plainly that it could not be confirmed either way. "
                              'Respond with exactly this JSON shape: {"verdict": "LIKELY_REAL" | "LIKELY_FAKE" | "UNVERIFIED", '
                              '"reasoning": "<max 500 chars, plain language, reference which stored sources or which red flag>"}.' + _JSON_ONLY),
}


class _Adjudication(BaseModel):
    decision: Literal["relevant", "not_relevant", "insufficient_evidence"]
    reason: str = Field(max_length=500)
    quote: str | None = None


class _ClaimItem(BaseModel):
    claim: str = Field(min_length=3, max_length=500)
    quote: str = Field(min_length=3, max_length=600)
    claim_type: str | None = None


class _Claims(BaseModel):
    claims: list[_ClaimItem] = Field(max_length=10)


class _LabelItem(BaseModel):
    label: str
    quote: str = Field(min_length=3, max_length=600)


class _Labels(BaseModel):
    labels: list[_LabelItem] = Field(max_length=6)


class _Sentence(BaseModel):
    text: str = Field(min_length=3, max_length=600)
    claim_refs: list[str] = []


class _Synthesis(BaseModel):
    sentences: list[_Sentence] = Field(max_length=8)
    insufficient_evidence: bool = False


class FactCheckVerdict(BaseModel):
    verdict: Literal["LIKELY_REAL", "LIKELY_FAKE", "UNVERIFIED"]
    reasoning: str = Field(max_length=500)


class CompanyDiscovery(BaseModel):
    canonical_name: str = Field(min_length=2, max_length=200)
    official_domain: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=10, max_length=2000)
    aliases: list[str] = Field(default_factory=list, max_length=30)
    industries: list[str] = Field(default_factory=list, max_length=30)
    keywords: list[str] = Field(default_factory=list, max_length=50)
    sources: list[str] = Field(min_length=1, max_length=20)


# ---- call wrapper: budget, logging, structured output -------------------------------------------------------------
async def _today_calls(session: AsyncSession) -> int:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(await session.scalar(select(func.count(AiUsage.id)).where(AiUsage.operation.like("llm:%"), AiUsage.created_at >= start)) or 0)


def _json_from(text: str) -> object:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise LLMOutputInvalid("no JSON object in the model answer")
    try:
        return json.loads(match.group(0))
    except ValueError as exc:
        raise LLMOutputInvalid("model answer is not valid JSON") from exc


async def run_structured(session: AsyncSession, operation: str, user: str, schema: type[BaseModel], *, article_id=None) -> BaseModel:
    provider = get_provider()
    if provider is None:
        raise LLMUnavailable("the LLM provider is not configured (LLM_PROVIDER)")
    s = get_settings()
    if await _today_calls(session) >= s.llm_daily_call_budget:
        raise LLMUnavailable(f"daily LLM call budget ({s.llm_daily_call_budget}) is used up")
    prompt = PROMPTS[operation]
    started, status, error, result = time.time(), "ok", None, None
    try:
        result = await provider.complete(prompt.system, user, max_tokens=s.llm_max_output_tokens)
        try:
            return schema.model_validate(_json_from(result.text))
        except ValidationError as exc:
            raise LLMOutputInvalid(f"answer does not match the schema: {exc.error_count()} error(s)") from exc
    except LLMOutputInvalid as exc:
        status, error = "invalid_output", str(exc)
        raise
    except LLMUnavailable as exc:
        status, error = "error", str(exc)
        raise
    except Exception as exc:  # noqa: BLE001 - network/provider bugs are one failure mode
        status, error = "error", f"{type(exc).__name__}: {str(exc)[:160]}"
        raise LLMUnavailable("LLM provider failed") from exc
    finally:
        total = (result.prompt_tokens or 0) + (result.completion_tokens or 0) if result else None
        cost = ((result.prompt_tokens or 0) / 1000 * s.llm_input_cost_per_1k_usd + (result.completion_tokens or 0) / 1000 * s.llm_output_cost_per_1k_usd) if result else None
        session.add(AiUsage(provider=provider.name, model=provider.model, operation=f"llm:{operation}", prompt_version=prompt.version,
                            prompt_tokens=result.prompt_tokens if result else None, completion_tokens=result.completion_tokens if result else None,
                            total_tokens=total, cost_usd=cost, latency_ms=int((time.time() - started) * 1000), status=status,
                            error=error[:300] if error else None, related_article_id=article_id))
        await session.commit()


async def discover_company(session: AsyncSession, user: str) -> CompanyDiscovery:
    return await run_structured(session, "discover_company", user, CompanyDiscovery)


# ---- quote verification -------------------------------------------------------------------------------------------
def locate_quote(article: Article, quote: str) -> tuple[str, int, int] | None:
    """(field, start, end) where ``quote`` occurs VERBATIM in the stored text (whitespace-flexible match, returning the
    stored spelling), or None. The returned slice is what gets stored as the evidence span."""
    quote = " ".join(quote.split())
    if len(quote) < 3:
        return None
    pattern = re.compile(r"\s+".join(re.escape(part) for part in quote.split()))
    for field in ("title", "summary", "raw_text"):
        value = getattr(article, field) or ""
        match = pattern.search(value)
        if match:
            return field, match.start(), match.end()
    return None


def _article_text(article: Article, limit: int = 6000) -> str:
    parts = [f"TITLE: {article.title or ''}", f"SUMMARY: {article.summary or ''}"]
    if article.raw_text and article.raw_text != article.summary:
        parts.append(f"TEXT: {article.raw_text}")
    return "\n".join(parts)[:limit]


def _tier_factor(tier: str | None) -> float:
    return taxonomy_cfg.TIER_CONFIDENCE_FACTOR.get(tier or "TIER_3", 0.75)


async def _source_of(session: AsyncSession, article: Article) -> Source | None:
    return await session.get(Source, article.source_id) if article.source_id else None


def _client_of(article: Article) -> str | None:
    try:
        return (json.loads(article.score_reason or "{}") or {}).get("entity")
    except ValueError:
        return None


# ---- operations ---------------------------------------------------------------------------------------------------
BORDERLINE_REASONS = {"BELOW_RELEVANCE_THRESHOLD", "WEAK_ACRONYM_NO_CONTEXT"}


async def adjudicate_relevance(session: AsyncSession, article_id, *, actor: str | None = None) -> dict:
    """Borderline items only (rejected at the relevance stage for a threshold/acronym reason). The result is queued for a
    human in ``review_items``; the article's stored decision is never changed by the model."""
    article = await session.get(Article, article_id)
    if article is None:
        raise LookupError("article not found")
    try:
        evidence = json.loads(article.score_reason or "{}")
    except ValueError:
        evidence = {}
    reasons = {c.get("reason") for c in evidence.get("rejected_candidates", [])} | {evidence.get("reason")}
    if article.processing_status != "rejected" or evidence.get("stage") != "relevance" or not (reasons & BORDERLINE_REASONS):
        return {"status": "not_borderline", "detail": "Only articles rejected at the relevance stage for a borderline reason are sent to the model."}
    client = evidence.get("entity")
    answer = await run_structured(session, "adjudicate_relevance", f"CLIENT: {client}\n{_article_text(article)}", _Adjudication, article_id=article.id)
    decision, located = answer.decision, locate_quote(article, answer.quote) if answer.quote else None
    if decision == "relevant" and located is None:
        decision = "insufficient_evidence"  # a decision without a verifiable quote is not accepted
    item = ReviewItem(object_type="llm_adjudication", target_table="articles", target_id=article.id, status="open" if decision == "relevant" else "dismissed",
                      reason=json.dumps({"client": client, "decision": decision, "reason": answer.reason, "quote": answer.quote if located else None,
                                         "span": list(located) if located else None, "prompt_version": PROMPTS["adjudicate_relevance"].version,
                                         "generated": True, "actor": actor}, ensure_ascii=False))
    session.add(item)
    await session.commit()
    return {"status": "ok", "decision": decision, "queued_for_review": decision == "relevant", "review_item_id": str(item.id)}


async def extract_claims(session: AsyncSession, article_id) -> dict:
    article = await session.get(Article, article_id)
    if article is None or article.processing_status != "accepted":
        raise LookupError("accepted article not found")
    answer = await run_structured(session, "extract_claims", _article_text(article), _Claims, article_id=article.id)
    source, client = await _source_of(session, article), _client_of(article)
    url = article.canonical_url or article.url
    created, dropped = [], 0
    for item in answer.claims:
        located = locate_quote(article, item.quote)
        if located is None:
            dropped += 1  # PDF s.13: a claim without a verified quote is dropped
            continue
        field, start, end = located
        span = (getattr(article, field) or "")[start:end]
        claim_key = hashlib.sha256(f"{item.claim.strip()}|{client or ''}|{url}|{span}".encode()).hexdigest()
        existing = await session.scalar(select(Claim).where(Claim.claim_key == claim_key))
        if existing:
            created.append(str(existing.id))
            continue
        claim = Claim(claim_text=" ".join(item.claim.split()), claim_key=claim_key, entity_key=(client or "").lower() or None,
                      claim_type=item.claim_type if item.claim_type in {"fact", "opinion", "forecast", "announcement", "financial", "other"} else "other",
                      confidence=round(0.5 * _tier_factor(source.tier if source else None), 2), verification_status="unreviewed",
                      evidence_json=[{"source_url": url, "provider": "stored_articles", "title": article.title or "", "snippet": span[:300],
                                      "published_at": article.published_at.isoformat() if article.published_at else None,
                                      "discovered_at": (article.fetched_at or article.created_at or datetime.now(timezone.utc)).isoformat(),
                                      "relevance_score": float(article.relevance_score or 0.0), "page_title": article.title, "content_span": span,
                                      "field": field, "start": start, "end": end, "extracted_by": "llm", "prompt_version": PROMPTS["extract_claims"].version}])
        session.add(claim)
        await session.flush()
        session.add(ClaimSource(claim_id=claim.id, article_id=article.id, excerpt=span[:500]))
        created.append(str(claim.id))
    await session.commit()
    return {"status": "ok", "claims": created, "dropped_without_verified_quote": dropped}


async def classify_gaps(session: AsyncSession, article_id) -> dict:
    """Unresolved classification: only articles the rules left without any label."""
    article = await session.get(Article, article_id)
    if article is None or article.processing_status != "accepted":
        raise LookupError("accepted article not found")
    if await session.scalar(select(ArticleLabel.id).where(ArticleLabel.article_id == article.id, ArticleLabel.method == "rule").limit(1)):
        return {"status": "not_a_gap", "detail": "The rules already labelled this article."}
    answer = await run_structured(session, "classify_labels", f"ALLOWED LABELS: {', '.join(taxonomy_cfg.LABELS)}\n{_article_text(article)}", _Labels, article_id=article.id)
    source, written, dropped = await _source_of(session, article), [], 0
    for item in answer.labels:
        located = locate_quote(article, item.quote)
        if item.label not in taxonomy_cfg.LABELS or located is None:
            dropped += 1
            continue
        field, start, end = located
        session.add(ArticleLabel(article_id=article.id, label=item.label, method="llm", taxonomy_version=taxonomy_cfg.TAXONOMY_VERSION,
                                 confidence=round(min(0.95, 0.45 + 0.1) * _tier_factor(source.tier if source else None), 2),
                                 evidence={"quote": (getattr(article, field) or "")[start:end], "field": field, "start": start, "end": end,
                                           "prompt_version": PROMPTS["classify_labels"].version, "generated": True}))
        written.append(item.label)
    await session.commit()
    return {"status": "ok", "labels": written, "dropped": dropped}


async def _claims_for_client(session: AsyncSession, client: str, limit: int) -> list[tuple[Claim, Article, Source | None]]:
    rows = (await session.execute(
        select(Claim, Article, Source).join(ClaimSource, ClaimSource.claim_id == Claim.id).join(Article, Article.id == ClaimSource.article_id)
        .outerjoin(Source, Source.id == Article.source_id)
        .where(Claim.entity_key == client.lower(), Claim.verification_status.in_(("unreviewed", "verified", "corroborated")),
               Article.processing_status == "accepted").order_by(Claim.created_at.desc(), Claim.id).limit(limit))).all()
    return list(rows)


async def synthesize(session: AsyncSession, client: str, *, kind: Literal["insight", "recommendation"] = "insight", limit: int = 12,
                     actor: str | None = None) -> dict:
    rows = await _claims_for_client(session, client, limit)
    if not rows:
        return {"status": "insufficient_evidence", "detail": "No stored, cited claims exist for this client; the model was not called.", "stored": None}
    refs = {f"C{i + 1}": row for i, row in enumerate(rows)}
    listing = "\n".join(f"{ref}: {claim.claim_text}" for ref, (claim, _a, _s) in refs.items())
    task = "Write up to 3 insight sentences." if kind == "insight" else "Write up to 3 recommended-action sentences a client team could review."
    answer = await run_structured(session, "synthesize", f"CLIENT: {client}\nTASK: {task}\nCLAIMS:\n{listing}", _Synthesis)
    kept, removed = [], 0
    for sentence in answer.sentences:
        valid = [r for r in dict.fromkeys(sentence.claim_refs) if r in refs]
        if not valid:
            removed += 1  # PDF s.13: a sentence with no valid citation is removed
            continue
        kept.append((sentence.text.strip(), valid))
    if answer.insufficient_evidence or not kept:
        return {"status": "insufficient_evidence", "removed_uncited_sentences": removed, "stored": None}
    used = sorted({r for _t, v in kept for r in v})
    publishers = {(refs[r][1].source_id) for r in used}
    best_tier = max((_tier_factor(refs[r][2].tier if refs[r][2] else None) for r in used), default=0.75)
    confidence = round(min(0.9, 0.35 + 0.15 * len(publishers)) * best_tier, 2)
    body = "\n".join(f"{text} [{', '.join(v)}]" for text, v in kept)
    claim_ids = [str(refs[r][0].id) for r in used]
    article_ids = sorted({str(refs[r][1].id) for r in used})
    if kind == "insight":
        entity = await session.scalar(select(Entity).where(Entity.canonical_key == client.lower(), Entity.entity_type == "ORG"))
        insight = Insight(client_entity_id=entity.id if entity else None, title=f"Generated synthesis: {client}", body=body,
                          insight_type="llm_synthesis", priority=confidence, status="draft")
        session.add(insight)
        await session.flush()
        for r in used:
            session.add(InsightClaim(insight_id=insight.id, claim_id=refs[r][0].id))
        stored = {"type": "insight", "id": str(insight.id), "status": "draft"}
    else:
        key = hashlib.sha1("|".join(claim_ids).encode()).hexdigest()[:16]
        if (dup := await session.scalar(select(Recommendation).where(Recommendation.client_key == client.lower(), Recommendation.opportunity_key == key,
                                                                      Recommendation.origin == "llm"))):
            return {"status": "ok", "generated": True, "kept_sentences": len(kept), "removed_uncited_sentences": removed, "confidence": dup.confidence,
                    "claims_used": claim_ids, "stored": {"type": "recommendation", "id": str(dup.id), "status": dup.status, "existing": True}}
        rec = Recommendation(client_key=client.lower(), opportunity_key=key, title=f"Generated actions: {client}",
                             action="\n".join(t for t, _v in kept), rationale=body, confidence=confidence, origin="llm", generated=True, status="draft",
                             claim_ids=claim_ids, article_ids=article_ids,
                             confidence_basis={"independent_publishers": len(publishers), "best_source_tier_factor": best_tier})
        session.add(rec)
        await session.flush()
        stored = {"type": "recommendation", "id": str(rec.id), "status": "draft"}
    await session.commit()
    return {"status": "ok", "generated": True, "kept_sentences": len(kept), "removed_uncited_sentences": removed, "confidence": confidence,
            "claims_used": claim_ids, "stored": stored, "prompt_version": PROMPTS["synthesize"].version, "actor": actor}


async def summarize_researcher(session: AsyncSession, person_id, *, context: str | None = None) -> dict:
    """Source-grounded summary of a person's recent stored work (PDF s.11): only after retrieval, every sentence cites the stored
    documents it used (D1..), uncited sentences are removed, and with no documents the model is not called. Not persisted."""
    from app.services import researchers
    detail = await researchers.researcher_detail(session, person_id)
    if detail is None:
        raise LookupError("researcher not found")
    docs = detail["recent_work"][:8]
    if not docs:
        return {"status": "insufficient_evidence", "detail": "No stored documents for this person; the model was not called."}
    refs = {f"D{i + 1}": d for i, d in enumerate(docs)}
    listing = "\n".join(f"{ref}: {d['title']} ({d['published_at'] or 'undated'}, {d['source']})" for ref, d in refs.items())
    task = f"Summarise recent work of {detail['name']}" + (f" and why it matters for: {context}" if context else "") + ". Cite document ids in claim_refs."
    answer = await run_structured(session, "synthesize", f"TASK: {task}\nCLAIMS (stored documents):\n{listing}", _Synthesis)
    kept, removed = [], 0
    for sentence in answer.sentences:
        valid = [r for r in dict.fromkeys(sentence.claim_refs) if r in refs]
        if not valid:
            removed += 1
            continue
        kept.append({"text": sentence.text.strip(), "citations": [{"ref": r, "article_id": refs[r]["article_id"], "url": refs[r]["url"]} for r in valid]})
    if answer.insufficient_evidence or not kept:
        return {"status": "insufficient_evidence", "removed_uncited_sentences": removed}
    return {"status": "ok", "generated": True, "person": detail["name"], "sentences": kept, "removed_uncited_sentences": removed,
            "prompt_version": PROMPTS["synthesize"].version}
