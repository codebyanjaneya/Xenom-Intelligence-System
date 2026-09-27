from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.providers.search import SearchResult
from app.services.search_quality import quality_gate
from app.services.search_scoring import score_result


NOW = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)


def _result(**overrides) -> SearchResult:
    values = {
        "title": "JCB robotics AI update",
        "url": "https://example.test/story",
        "snippet": "A useful robotics and artificial intelligence result.",
        "domain": "example.test",
        "discovered_at": NOW,
        "provider": "test",
    }
    values.update(overrides)
    return SearchResult(**values)


def test_quality_gate_rejects_invalid_missing_and_duplicate_results() -> None:
    decisions = quality_gate(
        [
            _result(url="not-a-url"),
            _result(title=""),
            _result(snippet=""),
            _result(),
            _result(url="https://example.test/story/"),
        ]
    )

    assert [decision.reason for decision in decisions] == [
        "rejected_invalid_url",
        "rejected_missing_title",
        "rejected_missing_content",
        "accepted",
        "rejected_duplicate_url",
    ]


def test_entity_and_topic_matching_raise_score() -> None:
    matched = score_result("JCB robotics", _result(), NOW)
    unrelated = score_result(
        "JCB robotics",
        _result(title="Weather update", snippet="A general result", domain="other.test"),
        NOW,
    )

    assert matched.entity_match == 30
    assert matched.topic_match > unrelated.topic_match
    assert matched.score > unrelated.score


def test_recency_and_source_quality_are_transparent() -> None:
    recent = score_result(
        "robotics", _result(domain="example.gov", published_at=NOW - timedelta(hours=2)), NOW
    )
    old = score_result(
        "robotics", _result(domain="example.test", published_at=NOW - timedelta(days=90)), NOW
    )

    assert recent.recency == 20
    assert recent.source_quality == 10
    assert old.recency == 2
    assert recent.score > old.score


def test_tie_breaking_is_deterministic() -> None:
    first = _result(url="https://example.test/a")
    second = _result(url="https://example.test/b")
    first_score = score_result("JCB robotics", first, NOW)
    second_score = score_result("JCB robotics", second, NOW)

    ordered = sorted(
        [(first, first_score), (second, second_score)],
        key=lambda pair: (-pair[1].score, pair[0].url),
    )
    assert [item[0].url for item in ordered] == ["https://example.test/a", "https://example.test/b"]