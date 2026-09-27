from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.providers.search import FixtureSearchProvider, SearchProviderError

FIXTURE = Path(__file__).parents[1] / "app" / "providers" / "search" / "fixtures" / "m6-search-results.json"


def test_fixture_provider_returns_deterministic_order() -> None:
    provider = FixtureSearchProvider(FIXTURE)

    first = asyncio.run(provider.search("AI robotics", 10))
    second = asyncio.run(provider.search("ai robotics", 10))

    assert [result.title for result in first] == [result.title for result in second]
    assert len(first) == 3
    assert first[0].domain == "synthetic.example.test"


def test_fixture_provider_enforces_limit() -> None:
    provider = FixtureSearchProvider(FIXTURE)

    results = asyncio.run(provider.search("AI robotics", 2))

    assert len(results) == 2


def test_malformed_fixture_fails_safely(tmp_path) -> None:
    fixture = tmp_path / "malformed.json"
    fixture.write_text(json.dumps({"not": "a list"}), encoding="utf-8")

    with pytest.raises(SearchProviderError, match="must contain a list"):
        asyncio.run(FixtureSearchProvider(fixture).search("AI robotics", 10))


def test_malformed_result_record_fails_safely(tmp_path) -> None:
    fixture = tmp_path / "malformed-result.json"
    fixture.write_text(
        json.dumps([{"queries": ["AI robotics"], "results": [{"title": "missing fields"}]}]),
        encoding="utf-8",
    )

    with pytest.raises(SearchProviderError, match="malformed result"):
        asyncio.run(FixtureSearchProvider(fixture).search("AI robotics", 10))