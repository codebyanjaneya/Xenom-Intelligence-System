"""Deterministic event extraction from stored text + its wiring into POST /intelligence/extract."""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.services.stored_events import MAX_EVENTS_PER_ARTICLE, event_type_for, extract_events, sentences
from tests.test_article_search import _seed

JCB = {"jcb": {"JCB"}}


def test_event_needs_entity_and_cue_in_same_sentence():
    body = "JCB is a company. Launch dates were discussed. The new excavator was launched on Monday."
    assert extract_events("a", {"raw_text": body}, JCB) == []          # entity sentence has no cue; cue sentences have no entity
    (ev,) = extract_events("a", {"raw_text": "Weather was mild. JCB launched the NXT excavator in Nepal."}, JCB)
    assert ev.event_type == "launch" and ev.quote == "JCB launched the NXT excavator in Nepal."
    assert ev.entity_keys == ("jcb",)


def test_quote_is_verbatim_slice_with_offsets():
    text_ = "Intro.  JCB signs MoU with Kato for cranes!  Outro."
    (ev,) = extract_events("a", {"summary": text_}, JCB)
    assert ev.field == "summary" and text_[ev.start:ev.end] == ev.quote == "JCB signs MoU with Kato for cranes!"


def test_cue_types_and_priority():
    cases = {"JCB acquires a rival firm.": "acquisition", "JCB raises funding round.": "funding",
             "JCB and Kato form a joint venture.": "partnership", "JCB signed an agreement.": "agreement",
             "JCB wins an order from NHAI.": "contract", "JCB appoints a new CEO.": "appointment",
             "JCB unveils a backhoe.": "launch", "JCB opens a new plant.": "expansion",
             "JCB announced results.": "announcement"}
    for sentence, expected in cases.items():
        assert event_type_for(sentence) == expected, sentence
    assert event_type_for("JCB launches product after acquiring firm.") == "acquisition"   # priority order
    assert event_type_for("JCB is a construction brand.") is None


def test_additional_opportunity_signal_cues_from_the_architecture_pdf():
    """The PDF's opportunity signal set (Section 9) beyond the original 9 cue types."""
    cases = {
        "JCB is hiring engineers for its design team.": "hiring",
        "JCB floats a tender for component supply.": "procurement",
        "The government launches a new subsidy scheme for manufacturers.": "government_initiative",
        "JCB enters the electric vehicle market.": "market_entry",
        "Rival firm recalls a faulty product line.": "competitor_weakness",
        "JCB targets the small-farm customer segment.": "new_customer_segment",
        "Researchers publish a peer-reviewed paper on new alloys.": "research_breakthrough",
    }
    for sentence, expected in cases.items():
        assert event_type_for(sentence) == expected, sentence


def test_whole_word_entity_and_cue_matching():
    assert extract_events("a", {"raw_text": "JCBX launched something."}, JCB) == []
    assert extract_events("a", {"raw_text": "JCB relaunchedd nothing."}, JCB) == []          # cue must start at a word boundary


def test_dedupe_limit_determinism_and_sentence_cap():
    many = " ".join(f"JCB launched model {i}." for i in range(8))
    a = extract_events("x", {"raw_text": many}, JCB)
    assert len(a) == MAX_EVENTS_PER_ARTICLE and a == extract_events("x", {"raw_text": many}, JCB)
    assert len({e.event_key for e in a}) == len(a)
    same = extract_events("x", {"summary": "JCB launched a bus.", "raw_text": "JCB launched a bus."}, JCB)
    assert len(same) == 1                                                                       # identical sentence once
    assert extract_events("x", {"raw_text": "JCB launched " + "word " * 100 + "."}, JCB) == []   # over-long sentence skipped
    assert [(lo, hi) for lo, hi in sentences("A b. C d!")] == [(0, 4), (5, 9)]


def _set_body(db_engine, article_id, raw_text):
    async def run():
        async with db_engine.begin() as c:
            await c.execute(text("update articles set raw_text=:r where id=:i"), {"r": raw_text, "i": article_id})
    asyncio.run(run())


def test_endpoint_populates_events_relationships_timeline(admin_client, db_engine):
    ids = _seed(db_engine, [
        {"title": "JCB launches autonomous excavator", "summary": "Robotics on site.", "entity": "JCB", "source": "S",
         "days_ago": 5},
        {"title": "Excavator demand rises", "summary": "JCB and ISM both mentioned.", "entity": "JCB", "source": "S",
         "days_ago": 1},
    ])
    _set_body(db_engine, ids["Excavator demand rises"], "Analysts note demand. JCB formed a partnership with Kato for excavators.")
    body = admin_client.post("/intelligence/extract", json={"query": "excavator"}).json()
    events = {e["event_key"]: e for e in body["events"]}
    assert sorted(e["event_type"] for e in events.values()) == ["launch", "partnership"]
    launch = next(e for e in events.values() if e["event_type"] == "launch")
    assert launch["title"] == "JCB launches autonomous excavator" and launch["event_time"] is None
    urls = {a["url"]: a["article_id"] for a in body["articles"]}
    assert urls[launch["evidence"][0]["source_url"]] == ids["JCB launches autonomous excavator"]   # exact source article
    assert launch["evidence"][0]["content_span"] == launch["title"] and launch["evidence"][0]["provider"] == "stored_articles"
    rels = body["relationships"]
    assert {(r["entity_key"], r["relationship_type"]) for r in rels} == {("jcb", "INVOLVES")}
    assert {r["event_key"] for r in rels} == set(events)                                           # only events with the entity
    assert all(r["evidence"][0]["content_span"] == events[r["event_key"]]["title"] for r in rels)
    tl = body["timeline"]                                                                          # older article first
    assert [t["position"] for t in tl] == [1, 2] and tl[0]["event_key"] == launch["event_key"]
    assert all(t["ordering_time_source"] == "publication_fallback" for t in tl)
    assert body["temporal_relationships"] == []


def test_no_event_without_cue_and_ism_only_sentence_has_no_jcb_relationship(admin_client, db_engine):
    _seed(db_engine, [{"title": "Excavator market", "summary": "JCB is discussed at length.", "entity": "JCB",
                       "source": "S"}])
    body = admin_client.post("/intelligence/extract", json={"query": "excavator"}).json()
    assert body["events"] == [] and body["relationships"] == [] and body["timeline"] == []
    assert body["entities"]                                                                        # entity aggregation unchanged


# ---- Phase 2.4 precision regressions (patterns observed in the live JCB corpus) -----------------------------------

def _events(body, entities=JCB, field="raw_text"):
    return extract_events("a", {field: body}, entities)


def test_significant_does_not_trigger_agreement():
    assert _events("JCB India has honored two customers for their significant contributions.") == []
    assert [e.event_type for e in _events("JCB signed an agreement with Kato.")] == ["agreement"]


def test_cue_inside_quotation_is_ignored():
    assert _events('JCB CEO said "we have consistently launched products in Nepal" on Monday.') == []
    assert _events("JCB said “we launched a new backhoe” during the event.") == []
    assert [e.event_type for e in _events('JCB launched a backhoe and said "great day".')] == ["launch"]
    assert _events('JCB said "we launched this') == []                                # unmatched opening quote masks the rest


def test_attribution_and_markup_lines_are_never_events():
    assert _events('Deepak Shetty | CEO of JCB India "JCB has partnered with Kato".') == []
    assert _events("<item><title>JCB India Launches 52-Tonne Excavator</title>") == []
    assert _events('&lt;p class="x"&gt;JCB India unveiled 10 new products.') == []


def test_entity_must_precede_the_cue():
    assert _events("Among the other new launches, the JCB 150 FM excavator delivers 8% better fuel efficiency.") == []
    assert _events("We have made investments in service outlets for JCB machines.") == []
    assert [e.event_type for e in _events("JCB India recently launched the NXT 215 excavator.")] == ["launch"]


def test_entity_must_be_close_to_weak_cues():
    far = "JCB " + "word " * 30 + "invested in a plant."
    assert _events(far) == []                                                         # investment: 40-char limit
    assert [e.event_type for e in _events("JCB invested in a new plant.")] == ["investment"]
    assert _events("Podder and Podder are heavily invested in skilling programmes.") == []   # no registered entity


def test_launch_cue_needs_word_boundary_on_both_sides():
    assert _events("JCB relaunched nothing.") == []


def test_entity_keys_are_only_the_preceding_entities():
    ents = {"jcb": {"JCB"}, "kato": {"Kato"}}
    (ev,) = extract_events("a", {"raw_text": "JCB and Kato form a joint venture."}, ents)
    assert ev.entity_keys == ("jcb", "kato")
    (ev,) = extract_events("a", {"raw_text": "JCB formed a joint venture with Kato."}, ents)
    assert ev.entity_keys == ("jcb",)                                                 # Kato follows the cue: not the actor


# ---- cross-article merging -----------------------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone  # noqa: E402

from app.services.stored_events import Occurrence, headline_tokens, merge_occurrences  # noqa: E402

D0 = datetime(2025, 12, 10, tzinfo=timezone.utc)


def _occ(quote, article, when=D0, etype="launch", keys=("jcb",), field="title"):
    from app.services.stored_events import EventCandidate
    return Occurrence(EventCandidate(f"ev-{article}-{abs(hash(quote)) % 999}", etype, field, 0, len(quote), quote, keys),
                      article, when)


def test_publisher_suffix_and_stemming_normalisation():
    a = headline_tokens("JCB India Launches 52-Tonne Excavator, Largest Machine - Realty Plus Magazine")
    b = headline_tokens("JCB India launched 52 tonne excavator largest machine")
    assert a == b


def test_same_story_from_two_sources_merges_headline_primary():
    long = _occ("JCB India today launched a 52 tonne excavator, its largest machine, at Excon in Delhi.", "a1", field="raw_text")
    head = _occ("JCB India launches 52 tonne excavator largest machine - Autocar", "a2")
    (g,) = merge_occurrences([long, head])
    assert [o.article_id for o in g][0] == "a2"                  # title field / shorter sentence is primary
    assert {o.article_id for o in g} == {"a1", "a2"}


def test_conservative_no_merge_cases():
    base = "JCB India launches 52 tonne excavator largest machine"
    a = _occ(base, "a1")
    assert len(merge_occurrences([a, _occ(base, "a2", etype="acquisition")])) == 2                   # different type
    assert len(merge_occurrences([a, _occ(base, "a2", keys=("jcb", "kato"))])) == 2                  # different entities
    assert len(merge_occurrences([a, _occ(base, "a2", when=D0 + timedelta(days=60))])) == 2          # months apart
    assert len(merge_occurrences([a, _occ(base, "a2", when=None)])) == 2                             # unknown date
    assert len(merge_occurrences([a, _occ("JCB India launches 10 new machines at bauma expo", "a2")])) == 2  # other story
    assert len(merge_occurrences([_occ("JCB launches NXT", "a1"), _occ("JCB launches NXT", "a2")])) == 2     # too short


def test_merge_is_deterministic_and_keeps_first_appearance_order():
    items = [_occ("JCB India launches 52 tonne excavator largest machine", "a1"),
             _occ("JCB India opens plant somewhere far away", "a2", etype="expansion"),
             _occ("JCB India launched 52 tonne excavator largest machine today", "a3")]
    g1 = merge_occurrences(items)
    assert [ [o.article_id for o in g] for g in g1] == [["a1", "a3"], ["a2"]] or \
           [sorted(o.article_id for o in g) for g in g1] == [["a1", "a3"], ["a2"]]
    assert g1 == merge_occurrences(items)


def test_endpoint_merges_same_story_and_keeps_all_sources(admin_client, db_engine):
    ids = _seed(db_engine, [
        {"title": "JCB India launches 52 tonne excavator largest machine", "summary": "Details inside.", "entity": "JCB",
         "source": "S1", "days_ago": 3},
        {"title": "JCB India launched 52 tonne excavator largest machine - Trade Mag", "summary": "More.", "entity": "JCB",
         "source": "S2", "days_ago": 2},
        {"title": "JCB India launches backhoe for farms in Punjab market", "summary": "Other.", "entity": "JCB",
         "source": "S3", "days_ago": 2},
    ])
    body = admin_client.post("/intelligence/extract", json={"query": "JCB India launches"}).json()
    assert body["raw_event_count"] == 3 and len(body["events"]) == 2                       # 52-tonne story merged, backhoe separate
    merged = next(e for e in body["events"] if e["source_count"] == 2)
    assert set(merged["article_ids"]) == {ids["JCB India launches 52 tonne excavator largest machine"],
                                          ids["JCB India launched 52 tonne excavator largest machine - Trade Mag"]}
    assert len(merged["evidence"]) == 2 and len({e["source_url"] for e in merged["evidence"]}) == 2   # every source kept
    tl = next(t for t in body["timeline"] if t["event_key"] == merged["event_key"])
    assert set(tl["article_ids"]) == set(merged["article_ids"])                            # provenance on the timeline
    rel = [r for r in body["relationships"] if r["event_key"] == merged["event_key"]]
    assert len(rel) == 1 and len(rel[0]["evidence"]) == 2
    order = [a["article_id"] for a in body["articles"]]
    plain = [h["article_id"] for h in admin_client.get("/search/articles", params={"q": "JCB India launches"}).json()["results"]]
    assert order == plain                                                                  # ranking untouched


def test_historical_background_clause_is_not_an_event():
    body = "Building on the JCB programme launched in 2022, the firm broadens its range."
    assert extract_events("a", {"raw_text": body}, JCB, published_year=2026) == []           # launched in 2022 < 2026
    assert [e.event_type for e in extract_events("a", {"raw_text": body}, JCB, published_year=2022)] == ["launch"]
    assert [e.event_type for e in extract_events("a", {"raw_text": body}, JCB)] == ["launch"]  # no date known: not filtered
    now = "JCB launched a new backhoe in 2026."
    assert [e.event_type for e in extract_events("a", {"raw_text": now}, JCB, published_year=2026)] == ["launch"]


def test_page_boilerplate_is_not_an_event():
    line = "Read this article in Francais Deutsch 10 December 2025 JCB signed an agreement."
    assert extract_events("a", {"raw_text": line}, JCB) == []


def test_expansion_needs_a_concrete_object():
    assert _events("JCB expands market lead in backhoes.") == []
    assert _events("Semicon India 2026 Highlights India's Expanding Semiconductor Ecosystem, ISM says.", {"ism": {"ISM"}}) == []
    assert _events("JCB, today inaugurated its very first diesel Telescopic Boom Platform, the T65D.") == []
    assert _events("JCB opens a discussion on pricing.") == []
    for ok in ("JCB opens a new plant in Pune.", "JCB inaugurated a new plant in Pune.", "JCB expands its manufacturing facility."):
        assert [e.event_type for e in _events(ok)] == ["expansion"], ok
