"""
Strict multi-signal entity matching.

Replaces generic substring matching with:
1. Exact entity matching
2. Confidence scoring from multiple signals
3. Exclusion rule checking
4. Context validation
5. Structured rejection logging
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.entity_config import ENTITY_CONFIGS, EntityConfig
from app.models import Article, Entity, EntityMention

logger = logging.getLogger(__name__)


@dataclass
class MatchSignal:
    """A single matching signal."""

    signal_type: str  # "exact_legal", "exact_alias", "domain", etc.
    score: int
    matched_text: str
    confidence: float = 1.0


@dataclass
class EntityMatchResult:
    """Result of entity matching for an article."""

    entity_key: str
    confidence_score: int
    signals: list[MatchSignal]
    matched: bool
    rejection_reason: Optional[str] = None
    matched_text: Optional[str] = None


class StrictEntityMatcher:
    """Multi-signal entity matcher with strict rules."""

    def __init__(self):
        self.configs = ENTITY_CONFIGS

    def match_entities_for_article(
        self,
        title: Optional[str],
        summary: Optional[str],
        source_domain: Optional[str],
    ) -> list[EntityMatchResult]:
        """
        Match entities for an article using multiple signals.

        Returns list of match results (both accepted and rejected).
        Rejected results include rejection reason for logging.
        """
        text = (title or "").lower() + " " + (summary or "").lower()
        results = []

        for entity_key, config in self.configs.items():
            result = self._match_entity(
                entity_key=entity_key,
                config=config,
                title=title or "",
                summary=summary or "",
                source_domain=source_domain or "",
                full_text=text,
            )
            results.append(result)

        return results

    def _match_entity(
        self,
        entity_key: str,
        config: EntityConfig,
        title: str,
        summary: str,
        source_domain: str,
        full_text: str,
    ) -> EntityMatchResult:
        """Match a single entity against article content."""

        signals: list[MatchSignal] = []
        matched_text: Optional[str] = None

        # 1. CHECK EXCLUDED ENTITIES FIRST (hard reject)
        excluded_match = self._check_excluded(full_text, config)
        if excluded_match:
            return EntityMatchResult(
                entity_key=entity_key,
                confidence_score=0,
                signals=[],
                matched=False,
                rejection_reason="ENTITY_EXCLUDED",
                matched_text=excluded_match,
            )

        # 2. CHECK FOR EXACT LEGAL NAME MATCH (high confidence)
        legal_match = self._match_exact_list(full_text, config.legal_names)
        if legal_match:
            signals.append(MatchSignal(
                signal_type="exact_legal_name",
                score=40,
                matched_text=legal_match,
            ))
            matched_text = legal_match

        # 3. CHECK FOR EXACT ALIAS MATCH
        alias_match = self._match_exact_list(full_text, config.aliases)
        if alias_match:
            signals.append(MatchSignal(
                signal_type="exact_alias",
                score=30,
                matched_text=alias_match,
            ))
            if not matched_text:
                matched_text = alias_match

        # 4. CHECK OFFICIAL DOMAIN (strong signal, can stand alone)
        if source_domain and self._match_domain(source_domain, config.official_domains):
            signals.append(MatchSignal(
                signal_type="official_domain",
                score=50,  # Increased: official domain is strong evidence
                matched_text=source_domain,
            ))

        # 5. CHECK SUBSIDIARIES
        if config.subsidiaries:
            subsidiary_match = self._match_exact_list(full_text, config.subsidiaries)
            if subsidiary_match:
                signals.append(MatchSignal(
                    signal_type="subsidiary",
                    score=25,
                    matched_text=subsidiary_match,
                ))
                if not matched_text:
                    matched_text = subsidiary_match

        # 6. CHECK PRODUCTS (count as signal, not just support)
        product_match = self._match_word_boundary_list(full_text, config.products)
        if product_match:
            signals.append(MatchSignal(
                signal_type="product_term",
                score=20,  # Increased from 15
                matched_text=product_match,
            ))

        # 7. CHECK INDUSTRY CONTEXT (support signal)
        industry_match = self._match_word_boundary_list(full_text, config.industry_terms)
        if industry_match:
            signals.append(MatchSignal(
                signal_type="industry_context",
                score=15,  # Increased from 10
                matched_text=industry_match,
            ))

        # 8. GENERIC KEYWORD CHECK (dangerous alone)
        # Only reject if NO other signals exist
        if not signals and self._is_generic_keyword_match(full_text, entity_key):
            return EntityMatchResult(
                entity_key=entity_key,
                confidence_score=0,
                signals=[],
                matched=False,
                rejection_reason="GENERIC_ACRONYM_ALONE",
                matched_text=entity_key,
            )

        # CALCULATE TOTAL CONFIDENCE
        total_score = sum(s.score for s in signals)

        # CHECK REQUIRED CONTEXT (only if we don't have strong signals)
        # Skip context check if we have official domain, legal name, or alias
        has_strong_signal = any(
            s.signal_type in ["official_domain", "exact_legal_name", "exact_alias"]
            for s in signals
        )

        if config.required_context_terms and signals and not has_strong_signal:
            context_present = any(
                self._match_word_boundary(full_text, term)
                for term in config.required_context_terms
            )
            if not context_present:
                return EntityMatchResult(
                    entity_key=entity_key,
                    confidence_score=0,
                    signals=signals,
                    matched=False,
                    rejection_reason="INSUFFICIENT_CONTEXT",
                    matched_text=matched_text,
                )

        # APPLY THRESHOLD
        passed_threshold = total_score >= config.confidence_threshold

        if not passed_threshold and signals:
            return EntityMatchResult(
                entity_key=entity_key,
                confidence_score=total_score,
                signals=signals,
                matched=False,
                rejection_reason="LOW_CONFIDENCE",
                matched_text=matched_text,
            )

        if not signals:
            return EntityMatchResult(
                entity_key=entity_key,
                confidence_score=0,
                signals=[],
                matched=False,
                rejection_reason="NO_ENTITY_SIGNALS",
                matched_text=None,
            )

        # ACCEPT
        return EntityMatchResult(
            entity_key=entity_key,
            confidence_score=total_score,
            signals=signals,
            matched=True,
            rejection_reason=None,
            matched_text=matched_text,
        )

    def _check_excluded(self, text: str, config: EntityConfig) -> Optional[str]:
        """Check if text matches any excluded entities."""
        # Check excluded entities
        for excluded in config.excluded_entities:
            if self._match_exact_phrase(text, excluded):
                return excluded

        # A full legal-name hit outranks generic exclusion keywords (e.g. "retail investors"
        # in an Action Construction Equipment share-price story). Excluded ENTITIES above
        # remain hard rejects.
        long_form = [n for n in config.legal_names if len(n) > 4]
        if self._match_exact_list(text, long_form):
            return None

        # Check excluded keywords in context
        excluded_keyword_pattern = "|".join(
            re.escape(kw) for kw in config.excluded_keywords
        )
        if excluded_keyword_pattern and re.search(excluded_keyword_pattern, text):
            match = re.search(excluded_keyword_pattern, text)
            if match:
                return match.group()

        return None

    def _match_exact_list(self, text: str, items: list[str]) -> Optional[str]:
        """Match exact phrase from list."""
        for item in items:
            if self._match_exact_phrase(text, item):
                return item
        return None

    def _match_exact_phrase(self, text: str, phrase: str) -> bool:
        """Match exact phrase (case insensitive, word boundary)."""
        pattern = r'\b' + re.escape(phrase.lower()) + r'\b'
        return bool(re.search(pattern, text))

    def _match_word_boundary_list(self, text: str, items: list[str]) -> Optional[str]:
        """Match word boundary from list."""
        for item in items:
            if self._match_word_boundary(text, item):
                return item
        return None

    def _match_word_boundary(self, text: str, term: str) -> bool:
        """Match term with word boundaries."""
        pattern = r'\b' + re.escape(term.lower()) + r'\b'
        return bool(re.search(pattern, text))

    def _match_domain(self, source_domain: str, allowed_domains: list[str]) -> bool:
        """Check if source domain matches allowed domains."""
        source_lower = source_domain.lower()
        return any(
            allowed.lower() in source_lower
            for allowed in allowed_domains
        )

    def _is_generic_keyword_match(self, text: str, entity_key: str) -> bool:
        """Check if only a generic keyword matches (not allowed)."""
        # Map entity keys to generic keywords
        generic_keywords = {
            "ACE": ["ace"],
            "VIKAS": ["vikas"],
            "ISM": ["ism"],
            "JCB": ["jcb"],
            "VIPS": ["vips"],
            "Definedge": ["definedge"],
        }

        keywords = generic_keywords.get(entity_key, [])
        if not keywords:
            return False

        # Check if ONLY the generic keyword appears
        # (no additional context)
        for keyword in keywords:
            if re.search(r'\b' + re.escape(keyword) + r'\b', text):
                return True

        return False
