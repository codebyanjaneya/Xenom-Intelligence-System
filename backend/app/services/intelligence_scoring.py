"""Phase-1 relevance scoring for intelligence pipeline.

Implements deterministic scoring (0-100) with configurable weights:
- Company/entity relevance: 30
- Product/entity match: 15
- Technology relevance: 10
- Industry relevance: 10
- Source quality: 15
- Freshness: 10
- Context relevance: 10
TOTAL = 100

Weights are configuration-driven for post-review tuning.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from dataclasses import dataclass

from app.services.freshness import calculate_freshness_score

__all__ = ["ScoreBreakdown", "score_article", "QUALITY_GATE_DEFAULT"]

logger = logging.getLogger(__name__)

# Default quality gate cutoff (0-100 scale)
# Phase-1: 40 allows articles with direct company mentions + basic context
# to pass; higher thresholds (60+) would reject most company-relevant articles
# that lack additional signals beyond entity match
QUALITY_GATE_DEFAULT = 40


# Configurable scoring weights (must sum to 100)
# Updated to include freshness as a configurable component
SCORING_WEIGHTS = {
    "company_relevance": 30,  # Entity/company name matches
    "product_match": 15,      # Company products mentioned
    "technology": 10,         # Tech keywords present
    "industry": 10,           # Industry keywords present
    "source_quality": 15,     # Source tier (TIER_1/2/3)
    "freshness": 10,          # Publication age (latest preferred)
    "context": 10,            # Summary quality and relevance
}

# Source quality tiers (maps tier to score 0-15)
SOURCE_QUALITY_SCORES = {
    "TIER_1": 15,  # Official sources, established publications
    "TIER_2": 10,  # Strong secondary sources
    "TIER_3": 5,   # Lower-confidence general sources
    "TIER_4": 0,   # Forums / user-generated content: discovery signal only
}

# Entity type weighting (for company relevance scoring)
ENTITY_TYPE_WEIGHTS = {
    "ORG": 1.0,  # Full weight for organization mentions
    "PRODUCT": 0.8,
    "TECHNOLOGY": 0.6,
    "INDUSTRY": 0.4,
    "PERSON": 0.5,
}


@dataclass
class ScoreBreakdown:
    """Detailed scoring breakdown for transparency."""

    total_score: int
    """Total score 0-100."""

    company_relevance: int
    """Company/entity match points (0-30)."""

    product_match: int
    """Product/service match points (0-15)."""

    technology: int
    """Technology relevance points (0-10)."""

    industry: int
    """Industry relevance points (0-10)."""

    source_quality: int
    """Source tier quality points (0-15)."""

    freshness: int
    """Publication date freshness points (0-10)."""

    context: int
    """Context/summary relevance points (0-10)."""

    reasons: list[str]
    """Human-readable explanation of score."""


def score_article(
    title: str | None,
    summary: str | None,
    published_at: datetime | None,
    source_tier: str = "TIER_3",
    entity_mention_count: int = 0,
    has_product_match: bool = False,
    technology_keywords_found: int = 0,
    industry_keywords_found: int = 0,
) -> ScoreBreakdown:
    """Calculate intelligence relevance score for an article.

    Phase-1 deterministic scoring based on:
    - Entity mention count (company relevance)
    - Product/service matching
    - Technology keyword presence
    - Industry keyword presence
    - Source quality tier
    - Publication freshness
    - Text context/summary quality

    Args:
        title: Article title
        summary: Article summary/context
        published_at: Publication date
        source_tier: Source quality tier (TIER_1, TIER_2, TIER_3)
        entity_mention_count: Number of company entities mentioned
        has_product_match: Whether article mentions company products
        technology_keywords_found: Count of technology keywords matched
        industry_keywords_found: Count of industry keywords matched

    Returns:
        ScoreBreakdown with detailed scoring.
    """
    breakdown = ScoreBreakdown(
        total_score=0,
        company_relevance=0,
        product_match=0,
        technology=0,
        industry=0,
        source_quality=0,
        freshness=0,
        context=0,
        reasons=[],
    )

    # 1. COMPANY RELEVANCE (0-30)
    # Reward multiple entity mentions (0, 1, 2+)
    if entity_mention_count >= 2:
        breakdown.company_relevance = 30
        breakdown.reasons.append(f"Multiple entity mentions ({entity_mention_count}): +30")
    elif entity_mention_count == 1:
        breakdown.company_relevance = 20
        breakdown.reasons.append("Single entity mention: +20")
    else:
        breakdown.company_relevance = 5
        breakdown.reasons.append("Indirect or generic reference: +5")

    # 2. PRODUCT/ENTITY MATCH (0-15)
    if has_product_match:
        breakdown.product_match = 15
        breakdown.reasons.append("Product/service match found: +15")
    else:
        breakdown.product_match = 0

    # 3. TECHNOLOGY RELEVANCE (0-10)
    # Scale by count (0-3 keywords)
    if technology_keywords_found >= 3:
        breakdown.technology = 10
        breakdown.reasons.append(f"High technology relevance ({technology_keywords_found} keywords): +10")
    elif technology_keywords_found == 2:
        breakdown.technology = 7
        breakdown.reasons.append("Moderate technology relevance (2 keywords): +7")
    elif technology_keywords_found == 1:
        breakdown.technology = 4
        breakdown.reasons.append("Low technology relevance (1 keyword): +4")
    else:
        breakdown.technology = 0

    # 4. INDUSTRY RELEVANCE (0-10)
    if industry_keywords_found >= 2:
        breakdown.industry = 10
        breakdown.reasons.append(f"High industry relevance ({industry_keywords_found} keywords): +10")
    elif industry_keywords_found == 1:
        breakdown.industry = 5
        breakdown.reasons.append("Low industry relevance (1 keyword): +5")
    else:
        breakdown.industry = 0

    # 5. SOURCE QUALITY (0-15)
    breakdown.source_quality = SOURCE_QUALITY_SCORES.get(source_tier, 5)
    breakdown.reasons.append(f"Source tier {source_tier}: +{breakdown.source_quality}")

    # 6. FRESHNESS (0-10)
    # Time-based component: boosts recent articles but doesn't override entity relevance
    breakdown.freshness = calculate_freshness_score(published_at)
    if breakdown.freshness > 0:
        age_label = ""
        if published_at:
            now = datetime.now(timezone.utc)
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)
            days_old = (now - published_at).days
            if days_old <= 1:
                age_label = "< 1 day"
            elif days_old <= 7:
                age_label = f"{days_old}d"
            elif days_old <= 30:
                age_label = f"{days_old}d"
            else:
                age_label = "old"
        breakdown.reasons.append(f"Freshness ({age_label}): +{breakdown.freshness}")
    else:
        breakdown.reasons.append("Freshness (older): +0")

    # 7. CONTEXT RELEVANCE (0-10)
    # Reward detailed summaries and longer titles
    context_score = 0
    if title and len(title) >= 60:
        context_score += 3
    if summary and len(summary) >= 150:
        context_score += 5
    if summary and ("intelligence" in summary.lower() or "report" in summary.lower()):
        context_score += 2

    breakdown.context = min(10, context_score)
    breakdown.reasons.append(f"Context quality: +{breakdown.context}")

    # Calculate total (max 100)
    breakdown.total_score = min(
        100,
        breakdown.company_relevance
        + breakdown.product_match
        + breakdown.technology
        + breakdown.industry
        + breakdown.source_quality
        + breakdown.freshness
        + breakdown.context,
    )

    return breakdown
