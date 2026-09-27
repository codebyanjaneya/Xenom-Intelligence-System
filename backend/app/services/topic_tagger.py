"""Deterministic topic classification for Phase-1 intelligence pipeline.

Tags articles with topics based on keyword matching and entity type associations.
Topics are configuration-driven so embeddings/LLM classification can replace them later.
"""

from __future__ import annotations

from enum import Enum

__all__ = ["Topic", "tag_article_topics"]


class Topic(str, Enum):
    """Supported topic classifications."""

    AI_TECHNOLOGY = "AI & Technology"
    """AI, machine learning, automation, digital transformation."""

    INDUSTRY = "Industry"
    """Industry-specific developments, trends, practices."""

    COMPANY = "Company"
    """Company-specific news, announcements, strategies."""

    COMPETITOR = "Competitor"
    """Competitor activity, market positioning."""

    RESEARCH = "Research"
    """Research, studies, academic findings."""


# Keyword-based topic classification.
# Maps topic to keywords that indicate that topic in article text.
TOPIC_KEYWORDS: dict[Topic, set[str]] = {
    Topic.AI_TECHNOLOGY: {
        "ai",
        "artificial intelligence",
        "machine learning",
        "ml",
        "deep learning",
        "neural network",
        "automation",
        "robot",
        "robotics",
        "autonomous",
        "algorithm",
        "iot",
        "internet of things",
        "cloud",
        "edge computing",
        "blockchain",
        "smart",
    },
    Topic.INDUSTRY: {
        "industry",
        "manufacturing",
        "logistics",
        "supply chain",
        "construction",
        "infrastructure",
        "retail",
        "finance",
        "fintech",
        "banking",
        "trading",
        "market",
        "sector",
        "trend",
    },
    Topic.COMPANY: {
        "company",
        "corporation",
        "business",
        "enterprise",
        "startup",
        "announcement",
        "press release",
        "ceo",
        "leadership",
        "strategy",
        "initiative",
        "launch",
    },
    Topic.COMPETITOR: {
        "competitor",
        "rival",
        "competition",
        "market share",
        "competitive",
        "threat",
    },
    Topic.RESEARCH: {
        "research",
        "study",
        "research paper",
        "academic",
        "university",
        "finding",
        "analysis",
        "report",
        "survey",
    },
}


def tag_article_topics(title: str | None, summary: str | None) -> list[Topic]:
    """Classify article with relevant topics.

    Uses deterministic keyword matching on title + summary.
    Multiple topics can apply to a single article.

    Args:
        title: Article title
        summary: Article summary/description

    Returns:
        List of applicable topics (empty if no match).
    """
    if not title and not summary:
        return []

    # Combine text and normalize
    text = f"{title or ''} {summary or ''}".lower()

    # Split into words for keyword matching
    words = set(text.split())

    # Also include bigrams/phrases for multi-word keywords
    text_phrases = text.split()

    topics: list[Topic] = []

    for topic, keywords in TOPIC_KEYWORDS.items():
        matched = False

        # Check for single-word keyword matches
        if any(word in words for word in keywords):
            matched = True

        # Check for multi-word phrase matches (space-separated keywords)
        if not matched:
            for keyword in keywords:
                if " " in keyword and keyword in text:
                    matched = True
                    break

        if matched and topic not in topics:
            topics.append(topic)

    return topics
