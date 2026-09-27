"""Regressions for defects demonstrated in the strict matcher (2026-09-20)."""

from __future__ import annotations

from app.services.strict_entity_matcher import StrictEntityMatcher

matcher = StrictEntityMatcher()


def result(key: str, title: str, summary: str = "") -> object:
    return next(r for r in matcher.match_entities_for_article(title, summary, "") if r.entity_key == key)


def test_full_legal_name_is_not_vetoed_by_generic_exclusion_keywords() -> None:
    """'retail investors' used to hard-reject a genuine Action Construction Equipment share-price story."""
    r = result("ACE", "Action Construction Equipment shares rise as retail investors pile in", "Order book strong; crane maker Q1 results")
    assert r.matched and r.rejection_reason is None


def test_excluded_entities_still_hard_reject_even_with_a_legal_name_nearby() -> None:
    r = result("ACE", "ACE Fashion launches spring collection", "Action Construction Equipment is unrelated")
    assert not r.matched and r.rejection_reason == "ENTITY_EXCLUDED"


def test_vips_and_ism_describe_the_real_organisations() -> None:
    assert result("ISM", "India Semiconductor Mission approves new fab", "Tata Electronics Dholera").matched
    assert result("VIPS", "Vivekananda Institute of Professional Studies signs MoU").matched
    assert not result("ISM", "ISM manufacturing index falls to 48", "Institute for Supply Management PMI report").matched


def test_matcher_is_only_a_candidate_generator_relevance_is_the_arbiter() -> None:
    """The matcher still over-matches crane/industry vocabulary without any client name (documented, by design):
    the relevance stage is what rejects it -- see test_client_relevance / test_pipeline_core."""
    from app.services.client_relevance import evaluate_relevance

    title, summary = "Escorts launches new mobile crane for India", "Construction equipment demand rises"
    assert result("ACE", title, summary).matched
    assert not evaluate_relevance("ACE", title=title, body=summary).accepted


def test_plural_vips_no_longer_creates_a_relevant_vips_article() -> None:
    from app.services.client_relevance import evaluate_relevance

    assert not evaluate_relevance("VIPS", title="VIPs attend the inauguration ceremony in Delhi").accepted
