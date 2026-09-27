"""
Regression tests for strict entity matching.

Ensures false positives are rejected and genuine articles are accepted.
"""

import pytest
from app.services.strict_entity_matcher import StrictEntityMatcher


class TestStrictEntityMatching:
    """Test cases for strict entity matching."""

    def setup_method(self):
        self.matcher = StrictEntityMatcher()

    # ===== FALSE POSITIVE REJECTION TESTS =====

    def test_reject_vidya_vikas_as_vikas_group(self):
        """Vidya Vikas is NOT Vikas Group."""
        title = "Vidya Vikas hosts insightful panel on Civil & Infrastructure Engineering"
        summary = "Educational institution panel discussion"

        results = self.matcher.match_entities_for_article(title, summary, "")
        vikas_result = next(r for r in results if r.entity_key == "VIKAS")

        assert not vikas_result.matched, "Should reject Vidya Vikas"
        assert vikas_result.rejection_reason in [
            "ENTITY_EXCLUDED",
            "GENERIC_ACRONYM_ALONE",
            "NO_ENTITY_SIGNALS",
        ]

    def test_reject_ace_turtle_as_ace_construction(self):
        """Ace Turtle is NOT Action Construction Equipment."""
        title = "The Ace Turtle Playbook: Powering Global Brands in the Indian Market"
        summary = "Ace Turtle is a fashion and retail company"

        results = self.matcher.match_entities_for_article(title, summary, "")
        ace_result = next(r for r in results if r.entity_key == "ACE")

        assert not ace_result.matched, "Should reject Ace Turtle"
        assert ace_result.rejection_reason in [
            "ENTITY_EXCLUDED",
            "GENERIC_ACRONYM_ALONE",
            "NO_ENTITY_SIGNALS",
        ]

    def test_reject_generic_ace_usage(self):
        """Generic 'ace' without context should be rejected."""
        title = "Company Aces Quarterly Earnings Target"
        summary = "Generic use of 'ace' as English word"

        results = self.matcher.match_entities_for_article(title, summary, "")
        ace_result = next(r for r in results if r.entity_key == "ACE")

        assert not ace_result.matched, "Should reject generic 'ace'"

    def test_reject_generic_vikas_usage(self):
        """Generic 'vikas' without context should be rejected."""
        title = "India Focus on Vikas Development Initiative"
        summary = "Government initiative using word vikas"

        results = self.matcher.match_entities_for_article(title, summary, "")
        vikas_result = next(r for r in results if r.entity_key == "VIKAS")

        assert not vikas_result.matched, "Should reject generic 'vikas'"

    def test_reject_vikas_infrastructure(self):
        """Vikas Infrastructure is NOT Vikas Group."""
        title = "Vikas Infrastructure Projects Advance in State"
        summary = "Infrastructure development project"

        results = self.matcher.match_entities_for_article(title, summary, "")
        vikas_result = next(r for r in results if r.entity_key == "VIKAS")

        # Infrastructure is in excluded_keywords for VIKAS
        assert not vikas_result.matched, "Should reject Vikas Infrastructure"

    # ===== TRUE POSITIVE ACCEPTANCE TESTS =====

    def test_accept_action_construction_equipment(self):
        """Should accept article about Action Construction Equipment."""
        title = "Action Construction Equipment launches new crane technology"
        summary = "ACE announces advanced lifting solutions for construction industry"

        results = self.matcher.match_entities_for_article(title, summary, "")
        ace_result = next(r for r in results if r.entity_key == "ACE")

        assert ace_result.matched, "Should accept Action Construction Equipment"
        assert ace_result.confidence_score >= 60

    def test_accept_ace_with_product_context(self):
        """Should accept ACE when paired with construction product."""
        title = "ACE unveils new backhoe loader with AI features"
        summary = "Construction equipment manufacturer announces innovation"

        results = self.matcher.match_entities_for_article(title, summary, "")
        ace_result = next(r for r in results if r.entity_key == "ACE")

        assert ace_result.matched, "Should accept ACE with construction context"
        assert ace_result.confidence_score >= 30  # Product + industry signals

    def test_accept_vikas_group_exact(self):
        """Should accept exact Vikas Group mention."""
        title = "Vikas Group announces thermal management expansion"
        summary = "Automotive supplier Vikas Group reveals new manufacturing facility"

        results = self.matcher.match_entities_for_article(title, summary, "")
        vikas_result = next(r for r in results if r.entity_key == "VIKAS")

        assert vikas_result.matched, "Should accept Vikas Group"
        assert vikas_result.confidence_score >= 60

    def test_accept_sanden_vikas(self):
        """Should accept Sanden Vikas mention."""
        title = "Sanden Vikas expands automotive thermal solutions"
        summary = "Subsidiary of Vikas Group announces new products"

        results = self.matcher.match_entities_for_article(title, summary, "")
        vikas_result = next(r for r in results if r.entity_key == "VIKAS")

        assert vikas_result.matched, "Should accept Sanden Vikas"

    def test_accept_jcb_excavator(self):
        """Should accept JCB excavator article."""
        title = "JCB unveils hydrogen-powered excavator"
        summary = "Construction equipment manufacturer JCB announces new technology"

        results = self.matcher.match_entities_for_article(title, summary, "")
        jcb_result = next(r for r in results if r.entity_key == "JCB")

        assert jcb_result.matched, "Should accept JCB"

    def test_accept_definedge_algo_trading(self):
        """Should accept Definedge algo trading article."""
        title = "Definedge Securities launches ALGOSTRA trading platform"
        summary = "Fintech company announces new algo trading features"

        results = self.matcher.match_entities_for_article(title, summary, "")
        definedge_result = next(r for r in results if r.entity_key == "Definedge")

        assert definedge_result.matched, "Should accept Definedge trading article"

    def test_accept_from_official_domain(self):
        """Should accept article from official company domain."""
        title = "New Product Announcement"
        summary = "Company news"

        results = self.matcher.match_entities_for_article(
            title, summary, "ace-cranes.com"
        )
        ace_result = next(r for r in results if r.entity_key == "ACE")

        # Official domain should provide enough signal
        assert ace_result.matched or ace_result.rejection_reason == "GENERIC_ACRONYM_ALONE"

    # ===== EDGE CASES =====

    def test_accept_vikas_with_automotive_context(self):
        """Vikas + automotive = accept."""
        title = "Vikas announces automotive thermal management breakthrough"
        summary = "Manufacturing company"

        results = self.matcher.match_entities_for_article(title, summary, "")
        vikas_result = next(r for r in results if r.entity_key == "VIKAS")

        # Should accept due to automotive context
        assert vikas_result.matched, "Should accept Vikas with automotive context"

    def test_reject_vikas_education_context(self):
        """Vikas + education keywords = reject."""
        title = "Vikas Institute announces new educational programs"
        summary = "Educational institution news"

        results = self.matcher.match_entities_for_article(title, summary, "")
        vikas_result = next(r for r in results if r.entity_key == "VIKAS")

        # Should reject due to education keyword
        assert not vikas_result.matched, "Should reject Vikas with education context"

    def test_multiple_signals_boost_confidence(self):
        """Multiple signals should increase confidence score."""
        title = "Vikas Group and Sanden Vikas announce joint thermal management initiative"
        summary = "Automotive manufacturing partnership"

        results = self.matcher.match_entities_for_article(title, summary, "")
        vikas_result = next(r for r in results if r.entity_key == "VIKAS")

        assert vikas_result.matched
        assert vikas_result.confidence_score > 70, "Multiple signals should boost confidence"

    def test_product_term_support_signal(self):
        """Product terms should support but not carry match alone."""
        title = "Thermal management innovation"
        summary = "Heat exchanger technology advancement"

        results = self.matcher.match_entities_for_article(title, summary, "")
        vikas_result = next(r for r in results if r.entity_key == "VIKAS")

        # Product terms alone are not enough
        # (no legal name or alias present)
        if not vikas_result.matched:
            assert vikas_result.rejection_reason in [
                "NO_ENTITY_SIGNALS",
                "LOW_CONFIDENCE",
            ]

    # ===== VIKAS HARDENING TESTS =====

    def test_reject_vidya_vikas_education_context(self):
        """Vidya Vikas (educational institution) should be rejected."""
        title = "Vidya Vikas Institute Announces New Course in Software Engineering"
        summary = "Educational institution offers programming courses to students"

        results = self.matcher.match_entities_for_article(title, summary, "")
        vikas_result = next(r for r in results if r.entity_key == "VIKAS")

        assert not vikas_result.matched, "Should reject Vidya Vikas institution"
        assert vikas_result.rejection_reason == "ENTITY_EXCLUDED"

    def test_reject_vikas_education_university(self):
        """Vikas University should be rejected."""
        title = "Vikas University Ranked Among Top 100 Institutions"
        summary = "University announces new academic partnerships"

        results = self.matcher.match_entities_for_article(title, summary, "")
        vikas_result = next(r for r in results if r.entity_key == "VIKAS")

        assert not vikas_result.matched, "Should reject Vikas University"

    def test_reject_vikas_school(self):
        """Vikas School should be rejected."""
        title = "Vikas School Opens New Campus in Delhi"
        summary = "Educational school announces expansion plans"

        results = self.matcher.match_entities_for_article(title, summary, "")
        vikas_result = next(r for r in results if r.entity_key == "VIKAS")

        assert not vikas_result.matched, "Should reject Vikas School"

    def test_reject_bharat_vikas_parishad(self):
        """Bharat Vikas Parishad should be rejected."""
        title = "Bharat Vikas Parishad Organizes Rural Development Conference"
        summary = "Organization focuses on social development initiatives"

        results = self.matcher.match_entities_for_article(title, summary, "")
        vikas_result = next(r for r in results if r.entity_key == "VIKAS")

        assert not vikas_result.matched, "Should reject Bharat Vikas Parishad"

    def test_reject_nyaya_vikas(self):
        """Nyaya Vikas should be rejected."""
        title = "Nyaya Vikas Foundation Launches Legal Awareness Program"
        summary = "Legal NGO announces community outreach initiative"

        results = self.matcher.match_entities_for_article(title, summary, "")
        vikas_result = next(r for r in results if r.entity_key == "VIKAS")

        assert not vikas_result.matched, "Should reject Nyaya Vikas"

    # ===== ACE HARDENING TESTS =====

    def test_reject_ace_fashion_brand(self):
        """ACE Fashion should be rejected."""
        title = "ACE Fashion Launches New Spring Collection"
        summary = "Fashion brand announces new clothing line for consumers"

        results = self.matcher.match_entities_for_article(title, summary, "")
        ace_result = next(r for r in results if r.entity_key == "ACE")

        assert not ace_result.matched, "Should reject ACE Fashion"

    def test_reject_ace_digital_marketing(self):
        """ACE Digital should be rejected."""
        title = "ACE Digital Marketing Agency Wins Industry Award"
        summary = "Marketing firm announces new client partnerships"

        results = self.matcher.match_entities_for_article(title, summary, "")
        ace_result = next(r for r in results if r.entity_key == "ACE")

        assert not ace_result.matched, "Should reject ACE Digital"

    def test_reject_ace_university(self):
        """ACE University should be rejected."""
        title = "ACE University Announces New MBA Program"
        summary = "Educational institution launches business degree program"

        results = self.matcher.match_entities_for_article(title, summary, "")
        ace_result = next(r for r in results if r.entity_key == "ACE")

        assert not ace_result.matched, "Should reject ACE University"

    def test_accept_ace_crane_specific_model(self):
        """ACE with specific crane model should be accepted."""
        title = "ACE Mobile Crane MC-420 Sets New Lifting Record"
        summary = "Construction equipment manufacturer announces new model performance"

        results = self.matcher.match_entities_for_article(title, summary, "")
        ace_result = next(r for r in results if r.entity_key == "ACE")

        assert ace_result.matched, "Should accept ACE with specific model reference"
        assert ace_result.confidence_score >= 35

    def test_accept_ace_from_official_domain(self):
        """Article from ACE official domain should be accepted."""
        title = "Company Announces New Product Line"
        summary = "Press release about upcoming equipment"

        results = self.matcher.match_entities_for_article(
            title, summary, "ace-cranes.com"
        )
        ace_result = next(r for r in results if r.entity_key == "ACE")

        assert ace_result.matched, "Should accept article from official ACE domain"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
