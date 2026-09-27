"""Search -> evidence: verbatim spans from stored text. Pure tests plus test-DB API tests."""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.services.article_evidence import MAX_QUOTE, collect_terms, find_spans
from tests.test_article_search import _seed


def test_spans_are_verbatim_slices_with_offsets():
    body = "Intro line. JCB launched the NXT excavator in Delhi today. Unrelated closing sentence."
    spans = find_spans({"title": "JCB news", "summary": None, "raw_text": body}, ["jcb", "excavator"])
    assert [s.field for s in spans] == ["title", "raw_text"]
    for s in spans:
        src = {"title": "JCB news", "raw_text": body}[s.field]
        assert src[s.start:s.end] == s.quote
    assert spans[1].quote == "JCB launched the NXT excavator in Delhi today."
    assert spans[1].terms == ["excavator", "jcb"]


def test_case_insensitive_whole_word_only():
    assert find_spans({"raw_text": "The jcbx machine and subjcb tools."}, ["jcb"]) == []
    assert len(find_spans({"raw_text": "the jcb machine."}, ["JCB"])) == 1


def test_no_terms_or_no_match_gives_no_spans_and_never_invents():
    assert find_spans({"raw_text": "anything"}, []) == []
    assert find_spans({"title": "alpha", "raw_text": "beta"}, ["gamma"]) == []


def test_limits_dedupe_and_determinism():
    body = " ".join(f"Sentence {i} mentions ism clearly." for i in range(10))
    a = find_spans({"raw_text": body}, ["ism"])
    assert len(a) == 3 and a == find_spans({"raw_text": body}, ["ism"])
    assert all(a[i].end <= a[i + 1].start for i in range(len(a) - 1))          # non-overlapping, in order
    same = find_spans({"summary": "ISM builds chips.", "raw_text": "ISM builds chips."}, ["ism"])
    assert len(same) == 1 and same[0].field == "summary"                        # identical quote not repeated


def test_long_sentence_is_clamped_but_still_verbatim():
    long = "word " * 200 + "jcb " + "word " * 200
    (s,) = find_spans({"raw_text": long}, ["jcb"])
    assert len(s.quote) <= MAX_QUOTE and "jcb" in s.quote and long[s.start:s.end] == s.quote


def test_collect_terms_uses_query_and_stored_evidence_only():
    rel = {"matched_terms": ["JCB"], "entities": [{"matched_terms": ["Backhoe"]}]}
    assert collect_terms("the JCB in India", rel) == ["backhoe", "india", "jcb"]  # stopword "the" dropped
    assert collect_terms("a", None) == []  # single letters carry no evidence


def _set_body(db_engine, article_id: str, raw_text: str) -> None:
    async def run() -> None:
        async with db_engine.begin() as c:
            await c.execute(text("update articles set raw_text=:r where id=:i"), {"r": raw_text, "i": article_id})
    asyncio.run(run())


def test_endpoint_returns_search_hits_with_spans_and_stored_evidence(admin_client, db_engine):
    ids = _seed(db_engine, [
        {"title": "JCB launches autonomous excavator", "summary": "robotics on site", "entity": "JCB", "source": "S"},
        {"title": "ISM chips", "summary": "excavator unrelated", "entity": "ISM", "source": "S"},
    ])
    _set_body(db_engine, ids["JCB launches autonomous excavator"],
              "Opening. The new JCB excavator is autonomous and built in India. Closing remarks.")
    r = admin_client.get("/search/articles/evidence", params={"q": "excavator"})
    assert r.status_code == 200, r.text
    body = r.json()
    plain = admin_client.get("/search/articles", params={"q": "excavator"}).json()
    assert [h["article_id"] for h in body["results"]] == [h["article_id"] for h in plain["results"]]  # same ranking
    hit = next(h for h in body["results"] if h["article_id"] == ids["JCB launches autonomous excavator"])
    stored = next(h for h in plain["results"] if h["article_id"] == hit["article_id"])
    assert hit["relevance"] == stored["relevance"] and hit["relevance"]["entity"] == "JCB"  # stored, unmodified
    assert hit["client"] == "JCB" and hit["spans"]
    assert any(s["field"] == "raw_text" and s["quote"] == "The new JCB excavator is autonomous and built in India."
               for s in hit["spans"])
    assert all(set(s) == {"field", "start", "end", "quote", "terms"} for s in hit["spans"])


def test_endpoint_filters_validation_and_empty(admin_client, db_engine):
    _seed(db_engine, [{"title": "ISM excavator", "entity": "ISM", "source": "S"}])
    assert admin_client.get("/search/articles/evidence", params={"q": "excavator", "entity_key": "jcb"}).json()["count"] == 0
    assert admin_client.get("/search/articles/evidence", params={"q": "  "}).status_code == 422
    assert admin_client.get("/search/articles/evidence", params={"q": "excavator", "limit": 51}).status_code == 422


def test_endpoint_rejects_anonymous(anon_client):
    assert anon_client.get("/search/articles/evidence", params={"q": "x"}).status_code in (401, 403)


def test_spans_never_contain_markup_and_fall_back_to_clean_text():
    from app.services.markup import has_markup, strip_markup
    dirty = "&lt;p class=&quot;x&quot;&gt;JCB India unveiled 10 new products.&lt;/p&gt; <a href=\"u\">JCB opens plant</a>."
    assert find_spans({"raw_text": dirty}, ["jcb"]) == []                          # only marked-up sentences -> no span
    spans = find_spans({"title": "JCB launches backhoe", "raw_text": dirty}, ["jcb"])
    assert [s.field for s in spans] == ["title"] and not any(has_markup(s.quote) for s in spans)
    assert has_markup("<title>x</title>") and has_markup("&lt;p&gt;") and not has_markup("Profit & Loss < 5%")
    assert strip_markup("&lt;a href=&quot;x&quot;&gt;JCB&lt;/a&gt; wins") == "JCB wins" and strip_markup("clean") == "clean"
