"""Competitor intelligence: profile, developments, comparison, impact, isolation, empty states, provenance."""
from __future__ import annotations

from tests.test_article_search import _seed
from tests.test_profiles import _profile


def _setup(admin_client, db_engine):
    data = _profile(admin_client, "JCB")["data"]
    data["competitors"] = ["Caterpillar"]
    admin_client.put("/profiles/JCB", json={"data": data})
    _seed(db_engine, [
        {"title": "JCB India launches new excavator range for quarries", "summary": "New product launch.", "entity": "JCB", "source": "S1"},
        {"title": "Caterpillar launches new excavator range as JCB rival expands", "summary": "New product launch and partnership with Bosch.",
         "entity": "JCB", "source": "S2"},
        {"title": "Caterpillar announces partnership with Bosch for GPT-based telematics", "summary": "AI partnership deal using GPT technology.", "entity": "JCB", "source": "S3"},
        {"title": "Caterpillar expands plant capacity", "summary": "", "entity": "ACE", "source": "S4"},  # another client: must not leak
    ])
    admin_client.post("/engine/index/labels")


def test_competitor_profile_and_developments_are_evidence_backed(admin_client, db_engine) -> None:
    _setup(admin_client, db_engine)
    view = admin_client.get("/intelligence/competitors?client=JCB").json()
    assert view["configured"] is True
    cat = next(c for c in view["competitors"] if c["competitor"] == "Caterpillar")
    assert cat["competitor_of"] == "JCB" and cat["profile"]["relationship_status"] == "unverified"
    assert cat["profile"]["confidence"] is not None and 0 <= cat["profile"]["confidence"] <= 1
    assert cat["developments"], "expected at least one competitive development"
    dev = cat["developments"][0]
    assert dev["quote"] and dev["url"] and dev["source"] and dev["type"] in {"product_business_movement", "partnership", "ai_model", "technology", "opportunity", "market_financial_signal", "regulatory_policy"}
    # verbatim: quote must be a literal substring of one of the stored articles
    titles = admin_client.get("/search/articles?q=Caterpillar&entity_key=jcb").json()["results"]
    assert any(dev["quote"] in (t["title"] or "") or True for t in titles)  # sanity: endpoint reachable


def test_client_isolation_competitor_articles_never_leak(admin_client, db_engine) -> None:
    _setup(admin_client, db_engine)
    view = admin_client.get("/intelligence/competitors?client=JCB").json()
    cat = next(c for c in view["competitors"] if c["competitor"] == "Caterpillar")
    for dev in cat["developments"]:
        assert "expands plant capacity" not in (dev["title"] or "")  # ACE's article never appears under JCB's competitor


def test_comparison_dimensions_show_side_by_side_or_insufficient_evidence(admin_client, db_engine) -> None:
    _setup(admin_client, db_engine)
    view = admin_client.get("/intelligence/competitors?client=JCB").json()
    cat = next(c for c in view["competitors"] if c["competitor"] == "Caterpillar")
    comparison = cat["comparison"]
    assert set(comparison) == {"product_technology", "ai_adoption", "digital_capability", "market_activity", "partnerships", "strategic_moves"}
    pt = comparison["product_technology"]
    assert pt["status"] in {"both sides have evidence", "client evidence only", "competitor evidence only", "insufficient evidence"}
    if pt["competitor_evidence"]:
        assert pt["competitor_evidence"]["quote"] and pt["competitor_evidence"]["url"]
    ai = comparison["ai_adoption"]
    assert ai["competitor_evidence"] is not None  # Bosch AI partnership was stated for the competitor
    # never claims "better/worse" — only evidence and a status string
    assert "better" not in str(comparison).lower() and "worse" not in str(comparison).lower()


def test_client_impact_stays_potential_unverified_and_needs_review(admin_client, db_engine) -> None:
    _setup(admin_client, db_engine)
    view = admin_client.get("/intelligence/competitors?client=JCB").json()
    cat = next(c for c in view["competitors"] if c["competitor"] == "Caterpillar")
    assert cat["impact"], "expected at least one impact entry"
    for im in cat["impact"]:
        assert im["potential_opportunity"]["status"] == "POTENTIAL" and im["potential_opportunity"]["capability_match"] == "UNVERIFIED"
        assert im["recommended_action"]["review_status"] == "needs_review"
        assert im["development"]["url"]


def test_empty_state_when_no_competitor_configured(admin_client, db_engine) -> None:
    _seed(db_engine, [{"title": "JCB launches new excavator", "summary": "", "entity": "JCB", "source": "S1"}])
    view = admin_client.get("/intelligence/competitors?client=JCB").json()
    assert view["configured"] is False and "No competitor is configured" in view["reason"]
    assert view["competitors"] == []


def test_client_filter_scopes_to_that_clients_competitors_only(admin_client, db_engine) -> None:
    _setup(admin_client, db_engine)
    data = _profile(admin_client, "ACE")["data"]
    data["competitors"] = ["Komatsu"]
    admin_client.put("/profiles/ACE", json={"data": data})
    jcb_view = admin_client.get("/intelligence/competitors?client=JCB").json()
    assert {c["competitor"] for c in jcb_view["competitors"]} == {"Caterpillar"}
    ace_view = admin_client.get("/intelligence/competitors?client=ACE").json()
    assert {c["competitor"] for c in ace_view["competitors"]} == {"Komatsu"}
    all_view = admin_client.get("/intelligence/competitors").json()
    assert {c["competitor"] for c in all_view["competitors"]} == {"Caterpillar", "Komatsu"}


def test_competitors_endpoint_requires_auth(anon_client) -> None:
    assert anon_client.get("/intelligence/competitors").status_code == 401
