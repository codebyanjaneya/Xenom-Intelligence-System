"""Multi-label taxonomy (PDF s.9): the label set, its first-pass signals and the opportunity-signal framework.

Configuration, not logic. A label is only ever assigned from literal terms found in the stored title/summary/text, a stored
relevance decision, a source class or a profile entry, and it always carries that evidence. Items that match nothing stay
unlabelled and searchable.
"""
from __future__ import annotations

TAXONOMY_VERSION = "1"

LABELS = (
    "company", "competitor", "industry", "technology", "ai_model", "research", "opportunity", "partnership",
    "product_business_movement", "regulatory_policy", "market_financial_signal",
)

# PDF s.9 "First-pass signal" column, expressed as literal terms (word-boundary, case-insensitive).
PARTNERSHIP_TERMS = ("joint venture", "partnership", "partners with", "partnered", "tie-up", "collaboration", "mou",
                     "memorandum of understanding", "alliance", "technology transfer")
PRODUCT_BUSINESS_TERMS = ("launch", "launches", "launched", "unveil", "unveils", "unveiled", "introduces", "new model", "order",
                          "orders", "contract", "bags", "wins", "secures", "supply agreement", "expansion", "expands", "new plant",
                          "new facility", "capacity", "greenfield", "inaugurates", "inaugurated", "commissioned")
REGULATORY_TERMS = ("policy", "ministry", "government", "regulation", "regulatory", "notification", "scheme", "subsidy",
                    "incentive", "approves", "approval", "cabinet", "sebi", "rbi", "meity", "tender")
FINANCIAL_TERMS = ("share price", "shares", "results", "revenue", "profit", "dividend", "quarter", "earnings", "order book",
                   "funding", "investment", "invests", "acquisition", "acquires", "stake", "valuation", "ipo")
RESEARCH_MARKERS = ("arxiv", "preprint", "paper", "researchers", "peer-reviewed", "journal", "study finds", "study published")
# Model-name dictionary for ai_model (PDF: "Model-name dictionary; LLM later"). Literal names only.
AI_MODEL_TERMS = ("gpt", "chatgpt", "claude", "gemini", "llama", "mistral", "mixtral", "qwen", "deepseek",
                  "stable diffusion", "midjourney", "hugging face", "huggingface",
                  "foundation model", "large language model", "llm", "diffusion model", "open-source model")

# Opportunity signals (PDF s.9). A detector only says "this situation is described"; it never confirms an opportunity.
OPPORTUNITY_SIGNALS: dict[str, tuple[str, ...]] = {
    "funding": ("funding round", "secures funding", "raises funding", "raised funding", "raises capital", "raises funds", "series a", "series b", "investment of"),
    "partnership": PARTNERSHIP_TERMS,
    "expansion": ("expands", "expansion", "new plant", "new facility", "greenfield", "capacity addition"),
    "hiring": ("hiring", "hires", "recruitment drive", "appoints", "appointed"),
    "new_product_or_technology": ("launches", "launched", "unveils", "unveiled", "introduces", "new model", "prototype"),
    "procurement": ("tender", "procurement", "request for proposal", "rfp", "bids invited", "order worth"),
    "government_initiative": ("scheme", "policy", "subsidy", "incentive", "government approves", "cabinet approves"),
    "market_entry": ("enters", "market entry", "forays into", "foray into", "debut in", "commences operations"),
    "competitor_weakness": ("recall", "shutdown", "halts production", "loss widens", "layoffs", "lays off", "insolvency"),
    "new_customer_segment": ("new customer segment", "new segment", "targets new", "expands into"),
    "research_breakthrough": ("breakthrough", "first-of-its-kind", "state-of-the-art", "record efficiency", "novel approach"),
}

TIER_CONFIDENCE_FACTOR = {"TIER_1": 1.0, "TIER_2": 0.9, "TIER_3": 0.75, "TIER_4": 0.5}
