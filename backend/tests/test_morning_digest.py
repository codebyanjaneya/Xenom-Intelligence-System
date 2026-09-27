"""Morning digest (manager-requested, PDF s.11/s.15): a saved query over the existing search/index."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from tests.test_article_search import _seed
from app.schemas.domain import EvidenceQuote, SignalRef
from app.services.digest_ranking import DigestCandidate, near_duplicate, rank_candidate, select_diverse


def _candidate(title: str, publisher: str, day: int, tier: str = "TIER_2") -> DigestCandidate:
    signal = SignalRef(article_id=uuid4(), title=title, url=f"https://{publisher}/{day}",
                       published_at=datetime(2026, 9, day, tzinfo=timezone.utc), source=publisher,
                       publisher=publisher, clients=[], matched_terms=["robot"],
                       evidence=EvidenceQuote(field="title", start=0, end=len(title), quote=title))
    return rank_candidate(DigestCandidate(signal=signal, theme="robotics", tier=tier,
                                          reliability=1.0, title_summary_match=1.0),
                          now=datetime(2026, 9, 23, tzinfo=timezone.utc))


def test_digest_ranking_prefers_fresh_reliable_and_suppresses_near_duplicates() -> None:
    fresh = _candidate("New robot logistics platform", "source-a", 22, "TIER_1")
    old = _candidate("New robot logistics platform", "source-b", 1, "TIER_1")
    assert near_duplicate(fresh, old)
    assert fresh.score > old.score


def test_digest_selection_limits_publishers_and_keeps_topic_pool_diverse() -> None:
    titles = ["Robot excavator launch", "Robot warehouse trial", "Robot factory alliance", "Robot inspection rollout"]
    candidates = [_candidate(title, "source-a", 20 - i) for i, title in enumerate(titles)]
    candidates += [_candidate("Robot warehouse deployment", "source-b", 18),
                   _candidate("Robot maintenance contract", "source-b", 16),
                   _candidate("Robot factory partnership", "source-c", 17)]
    chosen = select_diverse(candidates, 5, max_per_publisher=2)
    publishers = [item.signal.publisher for item in chosen]
    assert len(chosen) == 5
    assert publishers.count("source-a") <= 2
    assert len(set(publishers)) >= 3


def test_digest_is_empty_but_honest_with_no_stored_evidence(admin_client) -> None:
    r = admin_client.get("/intelligence/digest")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["worth_your_attention"] == [] and body["researchers"] == []
    assert all(s["status"] == "NO_EVIDENCE_IN_STORED_CORPUS" for s in body["sections"])
    assert "saved query" in body["note"]


def test_digest_surfaces_real_evidenced_theme_articles(admin_client, db_engine) -> None:
    _seed(db_engine, [
        {"title": "JCB unveils embodied AI robotics platform", "summary": "The embodied AI system controls the robot arm.",
         "entity": "JCB", "source": "S1"},
        {"title": "Generic company update", "summary": "Nothing about AI here.", "entity": "JCB", "source": "S2"},
    ])
    body = admin_client.get("/intelligence/digest").json()
    sections_by_key = {s["key"]: s for s in body["sections"]}
    assert sections_by_key["embodied_ai"]["status"] == "EVIDENCE_FOUND"
    assert sections_by_key["embodied_ai"]["article_count"] >= 1
    assert sections_by_key["robotics"]["article_count"] >= 1
    assert len(body["worth_your_attention"]) >= 1
    item = body["worth_your_attention"][0]
    assert item["url"] and item["evidence"]["quote"]
