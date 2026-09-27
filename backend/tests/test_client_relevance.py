"""Client-specific relevance: precision on positive/negative cases, explainability, taxonomy sanity."""

from __future__ import annotations

import pytest

from app.config.entity_config import ENTITY_CONFIGS
from app.services.client_relevance import (
    ACCEPT_THRESHOLD,
    evaluate_all_clients,
    evaluate_relevance,
    publisher_domain,
)

# (title, body, expected accepted client or None)
CASES = [
    # ---- JCB
    ("JCB India expands manufacturing capacity at Pune plant", "", "JCB"),
    ("JCB India announces new plant", "", "JCB"),
    ("JCB Hydromax sets hydrogen land speed record", "", "JCB"),
    ("Construction equipment market grows in India", "Backhoe loader demand rises", None),
    ("1998 JCB 214S SERIES 3 For Sale (89263838) from Purple Wave, Inc. in Manhattan, KS", "", None),
    ("JCB turns 30th anniversary into Japan-themed consumer campaign", "JCB card cashback offer", None),
    ("JCB Literary Prize shortlist announced", "JCB Prize for Literature", None),
    # ---- ACE
    ("Action Construction Equipment declares ₹2 per share final dividend for FY26 - scanx.trade", "", "ACE"),
    ("ACE share price rises after Q1 results", "", "ACE"),
    ("ACE launches new construction equipment", "", "ACE"),
    ("Action Construction Equipment shares rise as retail investors pile in", "", "ACE"),
    ("ACE and KATO Works form JV to make cranes in India", "", "ACE"),
    ("Escorts launches new mobile crane for India", "Construction equipment demand rises", None),
    ("Ace tennis tournament: Djokovic serves 20 aces", "", None),
    ("ACE inhibitors linked to lower blood pressure", "", None),
    ("Ace Hardware opens 100th store", "", None),
    # ---- VIPS
    ("VIPS announces new AI course", "", "VIPS"),
    ("Vivekananda Institute of Professional Studies signs MoU with industry", "", "VIPS"),
    ("VIPS-TC placement drive: top recruiters visit campus", "", "VIPS"),
    ("VIPs attend the inauguration ceremony in Delhi", "", None),
    ("Delhi colleges announce AI courses for students", "", None),
    ("Vivekananda Institute of Technology Bengaluru announces admissions", "", None),
    # ---- Definedge
    ("Definedge launches new wealth-tech platform", "", "Definedge"),
    ("Definedge raises funding round from Hemant Luthra and others", "", "Definedge"),
    ("Generic stock market update: Nifty ends flat", "", None),
    ("Zerodha and Upstox brokerage fees compared", "", None),
    # ---- ISM
    ("India Semiconductor Mission approves new fab", "", "ISM"),
    ("PM Modi Independence Day speech: India's big semiconductor push, 5-8 plants coming up in few years", "", "ISM"),
    ("Semicon India 2026: ISM CEO pitches mega gathering", "", "ISM"),
    ("Semiconductor demand rises globally as AI boom continues", "", None),
    ("TSMC reports record quarterly profit on chip demand", "", None),
    ("ISM manufacturing index falls to 48 in August", "Institute for Supply Management PMI", None),
    ("IIT (ISM) Dhanbad announces placement drive", "", None),
    # ---- Vikas
    ("Vikas Group announces EV technology initiative", "", "VIKAS"),
    ("Pranav Vikas showcases bus HVAC at Prawaas", "", "VIKAS"),
    ("Sanden Vikas India inaugurates new compressor plant", "", "VIKAS"),
    ("Vikas Group launches new housing project in Pune", "real estate", None),
    ("Vikas Sharma wins gold medal at national games", "", None),
    ("Nyaya Vikas portal launched by ministry", "", None),
]


@pytest.mark.parametrize("title,body,expected", CASES)
def test_precision_cases(title: str, body: str, expected: str | None) -> None:
    decisions = evaluate_all_clients(title=title, body=body)
    accepted = [k for k, d in decisions.items() if d.accepted]
    assert accepted == ([expected] if expected else []), {
        k: (d.decision, round(d.relevance_score, 2), d.rejection_reason) for k, d in decisions.items() if d.relevance_score
    }


def test_accepted_decision_is_explainable() -> None:
    d = evaluate_relevance(
        "JCB", title="JCB India expands manufacturing capacity at Pune plant",
        source_domain="https://www.autocarpro.in/news/x",
    )
    assert d.accepted and d.relevance_score >= ACCEPT_THRESHOLD / 100
    payload = d.as_dict()
    assert payload["entity"] == "JCB" and payload["decision"] == "accepted"
    assert "JCB India" in payload["matched_terms"]
    assert payload["matched_topics"], "business-action topics should be reported"
    assert payload["source_type"] == "trusted_news" and payload["source_domain"] == "autocarpro.in"
    cats = {e["category"] for e in payload["evidence"]}
    assert {"strong_name", "source_trust"} <= cats


def test_rejected_decision_keeps_diagnostics() -> None:
    d = evaluate_relevance("JCB", title="1998 JCB 214S For Sale from Purple Wave in Kansas")
    assert not d.accepted and d.rejection_reason == "HARD_NEGATIVE"
    assert "Purple Wave" in d.negatives_hit or "for sale" in [n.lower() for n in d.negatives_hit]


def test_acronym_without_context_is_weak_not_accepted() -> None:
    d = evaluate_relevance("VIKAS", title="Vikas Group celebrates founders day event")
    assert not d.accepted and d.rejection_reason == "WEAK_ACRONYM_NO_CONTEXT"


def test_strong_name_in_title_overrides_hard_negative() -> None:
    d = evaluate_relevance("JCB", title="JCB India unveils new backhoe loader; auction houses expect used-machine price impact")
    assert d.accepted


def test_body_only_single_mention_is_not_material() -> None:
    d = evaluate_relevance(
        "ACE", title="Ten stocks to watch this week", body="Analysts also track Action Construction Equipment among others."
    )
    assert not d.accepted, "a single passing mention must not count as client intelligence"


def test_official_source_supplies_identity_but_a_topical_signal_is_still_required() -> None:
    topical = evaluate_relevance("VIPS", title="Application Form - vacant seats admission 2026-27 notice", source_domain="vips.edu")
    assert topical.accepted and topical.source_type == "official"
    assert [e.category for e in topical.evidence if e.category == "official_source"], "must be explainable"
    # an official page with nothing topical (an old exam notice, site maintenance) is NOT relevance by itself
    for title in ("End Term Examination (June 2023)", "Website maintenance scheduled for Sunday night"):
        assert not evaluate_relevance("VIPS", title=title, source_domain="vips.edu").accepted, title
    with_name = evaluate_relevance("VIPS", title="VIPS-TC admissions open for 2026-27 programmes", source_domain="vips.edu")
    assert with_name.accepted and with_name.source_type == "official"


def test_official_source_ownership_is_per_client() -> None:
    """A VIPS notice page is 'official' for VIPS only -- never a trust boost for another client."""
    title = "JCB India expands manufacturing capacity at Pune plant"
    other = evaluate_relevance("JCB", title=title, source_domain="vips.edu", source_kind="official", source_client="VIPS")
    assert other.source_type == "news"
    owned = evaluate_relevance("VIPS", title="Placement drive: top recruiters visit campus", source_domain="cdn.example.org",
                               source_kind="official", source_client="VIPS")
    assert owned.accepted and owned.source_type == "official"


def test_ism_own_press_releases_need_topic_not_the_word_india() -> None:
    title = "Rajasthan gets its first Semiconductor Manufacturing unit, plant inaugurated"
    assert evaluate_relevance("ISM", title=title, source_domain="ism.gov.in").accepted
    assert not evaluate_relevance("ISM", title=title, source_domain="random-blog.example").accepted
    assert not evaluate_relevance("ISM", title="HIGHLIGHTS OF UNION BUDGET 2026-27", source_domain="ism.gov.in").accepted


def test_no_client_accepts_empty_or_generic_text() -> None:
    assert not any(d.accepted for d in evaluate_all_clients(title="", body="").values())
    assert not any(d.accepted for d in evaluate_all_clients(title="Markets close higher on strong earnings").values())


def test_publisher_domain_normalisation() -> None:
    assert publisher_domain("https://www.Economictimes.indiatimes.com/x") == "economictimes.indiatimes.com"
    assert publisher_domain("vips.edu") == "vips.edu"
    assert publisher_domain(None) == ""


# ---- taxonomy sanity -------------------------------------------------------------------------------------------


def test_all_six_clients_configured() -> None:
    assert set(ENTITY_CONFIGS) == {"VIKAS", "ACE", "ISM", "JCB", "Definedge", "VIPS"}


@pytest.mark.parametrize("key", list(ENTITY_CONFIGS))
def test_each_client_has_specific_evidence_and_sources(key: str) -> None:
    cfg = ENTITY_CONFIGS[key]
    assert cfg.strong_keywords or cfg.compound_rules, f"{key} needs at least one unambiguous evidence path"
    assert cfg.official_domains and cfg.trusted_sources and cfg.topic_keywords
    assert len({s.lower() for s in cfg.strong_keywords}) == len(cfg.strong_keywords)


def test_client_identities_match_the_real_organisations() -> None:
    assert "India Semiconductor Mission" in ENTITY_CONFIGS["ISM"].strong_keywords
    assert "ISM Limited" not in ENTITY_CONFIGS["ISM"].legal_names
    assert "Vivekananda Institute of Professional Studies" in ENTITY_CONFIGS["VIPS"].strong_keywords
    assert "retail" not in [t.lower() for t in ENTITY_CONFIGS["VIPS"].industry_terms]
    # the bare acronyms are only usable case-sensitively and with context
    for key, acronym in (("ACE", "ACE"), ("JCB", "JCB"), ("VIPS", "VIPS"), ("ISM", "ISM")):
        assert acronym in ENTITY_CONFIGS[key].case_sensitive_acronyms
        assert acronym not in ENTITY_CONFIGS[key].strong_keywords
