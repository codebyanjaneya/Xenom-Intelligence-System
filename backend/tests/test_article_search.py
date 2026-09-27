"""GET /search/articles: read-only lexical search over stored accepted articles (test DB only)."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _evidence(entity: str, publisher_url: str | None = None) -> str:
    extra = {"publisher_url": publisher_url} if publisher_url else {}
    return json.dumps({"decision": "accepted", "entity": entity, "source_type": "trusted_news",
                       "matched_terms": [entity], "relevance_score": 0.6, **extra})


def _seed(db_engine, rows: list[dict]) -> dict[str, str]:
    """rows: title, summary, entity, status, gate, days_ago, source. Returns title -> article id."""
    ids: dict[str, str] = {}

    async def run() -> None:
        async with db_engine.begin() as c:
            sources: dict[str, str] = {}
            for r in rows:
                sid = sources.get(r["source"])
                if sid is None:
                    sid = sources[r["source"]] = str(uuid.uuid4())
                    await c.execute(text(
                        "insert into sources (id, name, canonical_url, source_type, tier, reliability_score, is_active) "
                        "values (:i, :n, :u, 'RSS', 'TIER_2', 0.5, true)"),
                        {"i": sid, "n": r["source"], "u": f"https://{sid}.test/feed"})
                aid = str(uuid.uuid4())
                ids[r["title"]] = aid
                await c.execute(text(
                    "insert into articles (id, source_id, url, title, summary, published_at, processing_status, "
                    "quality_gate_passed, score_reason, canonical_url) values (:i,:s,:u,:t,:su,:p,:st,:g,:sr,:cu)"),
                    {"i": aid, "s": sid, "u": r.get("url") or f"https://a.test/{aid}", "t": r["title"], "su": r.get("summary", ""),
                     "p": NOW - timedelta(days=r.get("days_ago", 0)), "st": r.get("status", "accepted"),
                     "g": r.get("gate", True), "sr": _evidence(r["entity"], r.get("publisher_url")), "cu": r.get("canonical_url")})
                await c.execute(text(
                    "insert into entity_mentions (id, entity_id, article_id, mention_text) "
                    "select gen_random_uuid(), id, :a, :m from entities where canonical_key = :k"),
                    {"a": aid, "m": r["entity"], "k": r["entity"].lower()})

    asyncio.run(run())
    return ids


def _base(db_engine):
    return _seed(db_engine, [
        {"title": "JCB launches autonomous excavator", "summary": "robotics on site", "entity": "JCB",
         "source": "Src A", "days_ago": 10},
        {"title": "Construction market update", "summary": "JCB autonomous excavator pilots", "entity": "JCB",
         "source": "Src A", "days_ago": 1},
        {"title": "ISM semiconductor supply chain", "summary": "excavator unrelated", "entity": "ISM",
         "source": "Src B", "days_ago": 5},
        {"title": "JCB excavator rejected story", "summary": "", "entity": "JCB", "source": "Src A",
         "status": "rejected", "gate": False},
    ])


def test_returns_only_accepted_with_stored_evidence(admin_client, db_engine) -> None:
    ids = _base(db_engine)
    r = admin_client.get("/search/articles", params={"q": "excavator"})
    assert r.status_code == 200, r.text
    body = r.json()
    got = {h["article_id"] for h in body["results"]}
    assert ids["JCB excavator rejected story"] not in got
    assert got == {ids["JCB launches autonomous excavator"], ids["Construction market update"],
                   ids["ISM semiconductor supply chain"]}
    hit = next(h for h in body["results"] if h["article_id"] == ids["ISM semiconductor supply chain"])
    assert hit["client"] == "ISM" and hit["source"] == "Src B" and hit["source_class"] == "trusted_news"
    assert hit["relevance"] == json.loads(_evidence("ISM"))  # stored evidence, unmodified
    assert set(hit) >= {"article_id", "title", "summary", "url", "published_at", "client", "source",
                        "source_class", "relevance", "score"}


def test_title_match_outranks_summary_match_then_recency(admin_client, db_engine) -> None:
    ids = _base(db_engine)
    res = admin_client.get("/search/articles", params={"q": "autonomous excavator"}).json()["results"]
    assert res[0]["article_id"] == ids["JCB launches autonomous excavator"]  # title weight beats newer summary hit
    assert res[0]["score"] > res[1]["score"]


def test_recency_breaks_score_ties(admin_client, db_engine) -> None:
    ids = _seed(db_engine, [
        {"title": "Quantum widget news", "entity": "JCB", "source": "S", "days_ago": 30},
        {"title": "Quantum widget news", "entity": "JCB", "source": "S", "days_ago": 2},
    ])
    res = admin_client.get("/search/articles", params={"q": "quantum widget"}).json()["results"]
    assert len(res) == 2 and res[0]["score"] == res[1]["score"]
    assert res[0]["published_at"] > res[1]["published_at"]


def test_filters_client_source_dates(admin_client, db_engine) -> None:
    ids = _base(db_engine)
    def ask(**p):
        r = admin_client.get("/search/articles", params={"q": "excavator", **p})
        assert r.status_code == 200, r.text
        return {h["article_id"] for h in r.json()["results"]}
    assert ask(entity_key="ism") == {ids["ISM semiconductor supply chain"]}
    assert ask(entity_key="JCB") == {ids["JCB launches autonomous excavator"], ids["Construction market update"]}
    assert ask(source="src b") == {ids["ISM semiconductor supply chain"]}
    cut = (NOW - timedelta(days=6)).isoformat()
    assert ask(date_from=cut) == {ids["Construction market update"], ids["ISM semiconductor supply chain"]}
    assert ask(date_to=cut) == {ids["JCB launches autonomous excavator"]}
    assert ask(entity_key="vips") == set()
    assert len(admin_client.get("/search/articles", params={"q": "excavator", "limit": 1}).json()["results"]) == 1


def test_validation(admin_client) -> None:
    assert admin_client.get("/search/articles").status_code == 422
    assert admin_client.get("/search/articles", params={"q": "   "}).status_code == 422
    assert admin_client.get("/search/articles", params={"q": "x", "limit": 0}).status_code == 422


def test_viewer_allowed(viewer_client) -> None:
    assert viewer_client.get("/search/articles", params={"q": "excavator"}).status_code == 200


def test_anonymous_rejected(anon_client) -> None:
    assert anon_client.get("/search/articles", params={"q": "excavator"}).status_code in (401, 403)


def test_no_match_is_empty_not_error(admin_client, db_engine) -> None:
    _base(db_engine)
    body = admin_client.get("/search/articles", params={"q": "zzzunmatched"}).json()
    assert body["count"] == 0 and body["results"] == []


# ---- Phase 2.3: layered retrieval regressions -------------------------------------------------

def test_min_terms_required():
    from app.services.article_search import min_terms_required
    assert [min_terms_required(n) for n in (1, 2, 3, 4, 5, 6)] == [1, 2, 2, 3, 4, 4]


def _titles(client, **params):
    r = client.get("/search/articles", params=params)
    assert r.status_code == 200, r.text
    return [h["title"] for h in r.json()["results"]]


def test_partial_match_recovers_when_strict_and_finds_nothing(admin_client, db_engine) -> None:
    _seed(db_engine, [
        {"title": "zorblat quixel vantrum report", "entity": "JCB", "source": "S"},
        {"title": "zorblat only mention", "entity": "JCB", "source": "S"},
    ])
    # 'melkor' appears nowhere: strict AND would return nothing; 3 of 4 lexemes is enough
    assert _titles(admin_client, q="zorblat quixel vantrum melkor") == ["zorblat quixel vantrum report"]


def test_partial_match_floor_keeps_no_match_queries_empty(admin_client, db_engine) -> None:
    _seed(db_engine, [{"title": "zorblat quixel news", "entity": "JCB", "source": "S"}])
    assert _titles(admin_client, q="zorblat quixel melkor vantrum") == []       # only 2 of 4
    assert _titles(admin_client, q="zorblat melkor") == []                       # two terms stay strict


def test_full_match_outranks_partial_match_even_with_better_text_rank(admin_client, db_engine) -> None:
    _seed(db_engine, [
        {"title": "zorblat zorblat zorblat quixel", "summary": "zorblat quixel", "entity": "JCB", "source": "S"},
        {"title": "zorblat quixel vantrum", "entity": "JCB", "source": "S", "days_ago": 300},
    ])
    assert _titles(admin_client, q="zorblat quixel vantrum")[0] == "zorblat quixel vantrum"


def test_client_named_in_query_outranks_passing_mention(admin_client, db_engine) -> None:
    _seed(db_engine, [
        {"title": "jcb", "summary": "", "entity": "ISM", "source": "S"},                       # best text rank, other client
        {"title": "Report", "summary": "jcb mentioned once", "entity": "JCB", "source": "S"},  # weaker text, JCB client
    ])
    assert _titles(admin_client, q="jcb") == ["Report", "jcb"]


def test_operator_queries_stay_strict(admin_client, db_engine) -> None:
    _seed(db_engine, [{"title": "zorblat quixel vantrum", "entity": "JCB", "source": "S"},
                      {"title": "zorblat quixel news", "entity": "JCB", "source": "S"}])
    assert _titles(admin_client, q='"quixel vantrum" zorblat melkor') == []
    assert _titles(admin_client, q="zorblat quixel -vantrum") == ["zorblat quixel news"]


def test_stopword_only_query_returns_nothing(admin_client, db_engine) -> None:
    _seed(db_engine, [{"title": "the and of", "entity": "JCB", "source": "S"}])
    assert _titles(admin_client, q="the of and") == []


def test_filters_and_accepted_only_still_apply_to_partial_matches(admin_client, db_engine) -> None:
    _seed(db_engine, [
        {"title": "zorblat quixel vantrum", "entity": "JCB", "source": "S1"},
        {"title": "zorblat quixel vantrum", "entity": "ISM", "source": "S2"},
        {"title": "zorblat quixel vantrum", "entity": "JCB", "source": "S1", "status": "rejected", "gate": False},
    ])
    q = "zorblat quixel vantrum melkor"
    assert len(_titles(admin_client, q=q)) == 2
    assert len(_titles(admin_client, q=q, entity_key="ism")) == 1
    assert len(_titles(admin_client, q=q, source="s1")) == 1


def test_ordering_is_deterministic(admin_client, db_engine) -> None:
    _seed(db_engine, [{"title": f"zorblat quixel vantrum {i}", "entity": "JCB", "source": "S", "days_ago": i} for i in range(6)])
    a = admin_client.get("/search/articles", params={"q": "zorblat quixel vantrum melkor"}).json()
    assert a == admin_client.get("/search/articles", params={"q": "zorblat quixel vantrum melkor"}).json()


def test_publisher_url_is_returned_instead_of_the_discovery_wrapper(admin_client, db_engine) -> None:
    ids = _seed(db_engine, [
        {"title": "JCB launches backhoe wrapper test", "entity": "JCB", "source": "S",
         "url": "https://news.google.com/rss/articles/WRAP", "canonical_url": "https://publisher.test/jcb-backhoe"},
        {"title": "JCB launches loader without canonical", "entity": "JCB", "source": "S", "url": "https://plain.test/x"},
    ])
    hits = {h["article_id"]: h for h in admin_client.get("/search/articles", params={"q": "JCB launches"}).json()["results"]}
    assert hits[ids["JCB launches backhoe wrapper test"]]["url"] == "https://publisher.test/jcb-backhoe"
    assert hits[ids["JCB launches loader without canonical"]]["url"] == "https://plain.test/x"       # falls back to stored url
    ev = admin_client.get("/search/articles/evidence", params={"q": "JCB launches"}).json()["results"]
    assert "news.google.com" not in " ".join(h["url"] for h in ev)


def test_markup_in_stored_titles_is_not_returned(admin_client, db_engine) -> None:
    ids = _seed(db_engine, [{"title": "&lt;b&gt;JCB launches marked up backhoe&lt;/b&gt;", "entity": "JCB", "source": "S"}])
    hit = admin_client.get("/search/articles", params={"q": "JCB backhoe"}).json()["results"][0]
    assert hit["title"] == "JCB launches marked up backhoe" and "&lt;" not in hit["title"]
