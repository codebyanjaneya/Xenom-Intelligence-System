"""End-to-end pipeline core against the isolated test DB (no network)."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.adapters.ingestion.base import NormalizedDocument
from app.models import Article, Entity, EntityMention, Source
from app.services import intelligence_pipeline as pipeline
from app.services.intelligence_pipeline import PipelineContext, process_documents
from tests.conftest import _truncate

NOW = datetime.now(timezone.utc)


@pytest.fixture
def maker(db_engine):
    m = async_sessionmaker(db_engine, expire_on_commit=False)
    asyncio.run(_truncate(m))
    return m


def doc(source: Source, title: str, *, body: str = "", url: str | None = None, hours_old: float = 2,
        domain: str = "autocarpro.in", **meta) -> NormalizedDocument:
    return NormalizedDocument(
        source_id=source.id,
        canonical_url=url or f"https://{domain}/news/{uuid.uuid4().hex[:10]}",
        title=title,
        published_at=NOW - timedelta(hours=hours_old),
        retrieved_at=NOW,
        raw_content=body or title,
        normalized_content=body or title,
        content_type="text/html",
        metadata={"publisher_domain": domain, **meta},
    )


async def new_source(session, name="Test feed", tier="TIER_2") -> Source:
    src = Source(name=name, canonical_url=f"https://{uuid.uuid4().hex[:8]}.example/feed", source_type="RSS", tier=tier)
    session.add(src)
    await session.flush()
    return src


def run_pipeline(maker, docs_fn, **kw):
    async def go():
        async with maker() as session:
            source = await new_source(session)
            docs = docs_fn(source)
            result = await process_documents(session, source, docs, ctx=kw.pop("ctx", PipelineContext(resolve_media=False)), **kw)
            return result, source.id

    return asyncio.run(go())


def rows(maker, source_id=None):
    async def go():
        async with maker() as session:
            stmt = select(Article)
            if source_id:
                stmt = stmt.where(Article.source_id == source_id)
            return list(await session.scalars(stmt.order_by(Article.title)))

    return asyncio.run(go())


def mentions(maker, article_id):
    async def go():
        async with maker() as session:
            res = await session.execute(
                select(Entity.canonical_key).join(EntityMention, EntityMention.entity_id == Entity.id).where(EntityMention.article_id == article_id)
            )
            return sorted(r[0] for r in res)

    return asyncio.run(go())


def test_relevant_article_is_accepted_with_client_mention_and_explainable_evidence(maker) -> None:
    result, sid = run_pipeline(maker, lambda s: [doc(s, "JCB India expands manufacturing capacity at Pune plant")])
    assert (result.documents_accepted, result.documents_rejected, result.documents_tagged_entity) == (1, 0, 1)
    (art,) = rows(maker, sid)
    assert art.processing_status == "accepted" and art.quality_gate_passed is True
    assert mentions(maker, art.id) == ["jcb"]
    evidence = json.loads(art.score_reason)
    assert evidence["decision"] == "accepted" and evidence["entity"] == "JCB"
    assert "JCB India" in evidence["matched_terms"] and evidence["matched_topics"]
    assert evidence["source_type"] == "trusted_news" and 0 < evidence["relevance_score"] <= 1
    assert evidence["quality"]["score"] >= evidence["quality"]["threshold"]
    assert art.relevance_score is not None and 0.4 <= art.relevance_score <= 1


def test_irrelevant_articles_are_rejected_persisted_with_diagnostics_and_never_listed(maker) -> None:
    titles = [
        "Construction equipment market grows in India as demand for backhoe loaders rises",
        "1998 JCB 214S SERIES 3 For Sale (89263838) from Purple Wave in Manhattan, KS",
        "Delhi colleges announce new AI courses for students this admission season",
        "Semiconductor demand rises globally as the AI boom continues into 2027",
        "Generic stock market update: Nifty ends flat as banks drag benchmark index",
    ]
    result, sid = run_pipeline(maker, lambda s: [doc(s, t) for t in titles])
    assert result.documents_accepted == 0 and result.documents_rejected == 5 and result.documents_tagged_entity == 0
    stored = rows(maker, sid)
    assert len(stored) == 5, "rejected records are kept for diagnostics and future dedupe"
    for art in stored:
        assert art.processing_status == "rejected" and art.quality_gate_passed is False
        info = json.loads(art.score_reason)
        assert info["decision"] == "rejected" and info["stage"] == "relevance" and info["reason"]
        assert info["rejected_candidates"]
        assert mentions(maker, art.id) == []
    assert result.rejection_reasons["RELEVANCE:HARD_NEGATIVE"] >= 1
    assert result.rejection_reasons["RELEVANCE:NO_ENTITY_EVIDENCE"] >= 3


def test_no_entity_article_cannot_pass_however_good_its_other_scores(maker) -> None:
    """Regression: previously 5(indirect)+source+freshness+context could reach the quality gate with zero entity match."""
    long_summary = "Detailed industry report intelligence summary. " * 12
    result, sid = run_pipeline(
        maker,
        lambda s: [doc(s, "A long and detailed headline about industrial manufacturing automation and supply chains in India", body=long_summary, hours_old=1)],
    )
    assert result.documents_accepted == 0
    (art,) = rows(maker, sid)
    assert json.loads(art.score_reason)["stage"] == "relevance"


def test_structurally_invalid_records_are_reported_and_not_persisted(maker) -> None:
    def make(s):
        good = doc(s, "JCB India inaugurates dealer head office in Vadodara")
        empty = doc(s, "")
        short = doc(s, "News")
        future = NormalizedDocument(**{**vars(doc(s, "JCB India plans new plant expansion")), "published_at": NOW + timedelta(days=30)})
        bad_url = doc(s, "JCB India launches new backhoe loader model", url="not-a-url")
        return [good, empty, short, future, bad_url]

    result, sid = run_pipeline(maker, make)
    assert result.documents_parsed == 5 and result.documents_invalid == 4 and result.documents_accepted == 1
    assert len(rows(maker, sid)) == 1
    assert {"STRUCTURAL:EMPTY_TITLE", "STRUCTURAL:TITLE_TOO_SHORT", "STRUCTURAL:FUTURE_DATE", "STRUCTURAL:INVALID_URL"} <= set(result.rejection_reasons)
    assert result.samples["invalid"]


def test_missing_date_is_rejected_only_when_required(maker) -> None:
    def make(s):
        d = doc(s, "Vivekananda Institute of Professional Studies announces admissions 2026-27")
        return [NormalizedDocument(**{**vars(d), "published_at": None})]

    strict, _ = run_pipeline(maker, make, ctx=PipelineContext(require_date=True))
    assert strict.documents_invalid == 1 and strict.documents_accepted == 0
    lenient, _ = run_pipeline(maker, make, ctx=PipelineContext(require_date=False, source_kind="official"))
    assert lenient.documents_accepted == 1


def test_quality_gate_still_applies_after_relevance_and_is_not_weakened(maker) -> None:
    result, sid = run_pipeline(
        maker, lambda s: [doc(s, "JCB India expands manufacturing capacity at Pune plant")], quality_gate_threshold=99
    )
    assert result.documents_tagged_entity == 1 and result.documents_quality_passed == 0 and result.documents_rejected == 1
    (art,) = rows(maker, sid)
    info = json.loads(art.score_reason)
    assert info["stage"] == "quality_gate" and info["reason"] == "BELOW_QUALITY_THRESHOLD"
    assert mentions(maker, art.id) == [], "no client mention for a gated-out article"


def test_multi_client_article_is_tagged_for_every_accepted_client(maker) -> None:
    result, sid = run_pipeline(
        maker, lambda s: [doc(s, "JCB India and Action Construction Equipment expand crane and backhoe loader capacity")]
    )
    assert result.documents_accepted == 1
    (art,) = rows(maker, sid)
    assert mentions(maker, art.id) == ["ace", "jcb"]
    assert {e["entity"] for e in json.loads(art.score_reason)["entities"]} == {"ACE", "JCB"}
    assert result.per_client_accepted["JCB"] == 1 and result.per_client_accepted["ACE"] == 1


def test_in_source_and_global_url_duplicates_are_skipped_without_losing_earlier_articles(maker) -> None:
    """Regression: an IntegrityError used to roll back the whole run, discarding earlier accepted articles."""

    async def go():
        async with maker() as session:
            s1, s2 = await new_source(session, "A"), await new_source(session, "B")
            shared = "https://autocarpro.in/news/shared-story"
            first = [doc(s1, "JCB India inaugurates dealer head office in Vadodara"),
                     doc(s1, "ACE Cranes bags large order for pick and carry cranes", url=shared)]
            await process_documents(session, s1, first, ctx=PipelineContext())
            second = [doc(s2, "Sanden Vikas India inaugurates new compressor plant"),
                      doc(s2, "ACE Cranes bags large order for pick and carry cranes", url=shared),   # same URL, other source
                      doc(s1, "JCB India inaugurates dealer head office in Vadodara")]                # same source & title/url differs
            return await process_documents(session, s2, second, ctx=PipelineContext())

    result = asyncio.run(go())
    assert result.documents_accepted == 1 and result.documents_deduplicated == 2 and not result.errors
    titles = sorted(a.title for a in rows(maker))
    assert titles == sorted([
        "ACE Cranes bags large order for pick and carry cranes",
        "JCB India inaugurates dealer head office in Vadodara",
        "Sanden Vikas India inaugurates new compressor plant",
    ])


def test_same_headline_from_another_source_is_a_cross_source_duplicate(maker) -> None:
    async def go():
        async with maker() as session:
            a, b = await new_source(session, "Wire A"), await new_source(session, "Wire B")
            await process_documents(session, a, [doc(a, "Action Construction Equipment shares rise on Q1 results - Wire A", domain="a.example")], ctx=PipelineContext())
            return await process_documents(
                session, b, [doc(b, "Action Construction Equipment shares rise on Q1 results - Wire B", domain="b.example", hours_old=5)], ctx=PipelineContext()
            )

    result = asyncio.run(go())
    assert result.documents_cross_deduplicated == 1 and result.documents_accepted == 0
    dup = [a for a in rows(maker) if a.processing_status == "rejected"]
    assert len(dup) == 1 and json.loads(dup[0].score_reason)["stage"] == "duplicate"
    assert json.loads(dup[0].score_reason)["duplicate_of"]


def test_one_failing_document_does_not_discard_the_rest(maker, monkeypatch) -> None:
    real = pipeline.attach_mention

    async def flaky(session, article, entity_key, text):
        if "Vikas" in (article.title or ""):
            raise RuntimeError("boom")
        return await real(session, article, entity_key, text)

    monkeypatch.setattr(pipeline, "attach_mention", flaky)
    result, sid = run_pipeline(maker, lambda s: [
        doc(s, "JCB India inaugurates dealer head office in Vadodara"),
        doc(s, "Sanden Vikas India inaugurates new compressor plant"),
        doc(s, "Action Construction Equipment declares final dividend for FY26"),
    ])
    assert result.documents_accepted == 2 and len(result.errors) == 1 and "boom" in result.errors[0]
    assert sorted(a.title for a in rows(maker, sid) if a.processing_status == "accepted") == [
        "Action Construction Equipment declares final dividend for FY26",
        "JCB India inaugurates dealer head office in Vadodara",
    ]


def test_google_wrapper_urls_are_never_stored_as_canonical(maker) -> None:
    wrapper = "https://news.google.com/rss/articles/CBMiAU_yqLtest?oc=5"
    result, sid = run_pipeline(maker, lambda s: [doc(s, "JCB India inaugurates dealer head office in Vadodara", url=wrapper, wrapper_url=True)])
    (art,) = rows(maker, sid)
    assert art.url == wrapper and art.canonical_url is None and art.image_url is None
    assert result.documents_accepted == 1


def test_rss_media_image_is_kept_only_if_it_is_a_real_article_image(maker) -> None:
    result, sid = run_pipeline(maker, lambda s: [
        doc(s, "JCB India inaugurates dealer head office in Vadodara", image_url="https://cdn.autocarpro.in/2026/09/hero.jpg"),
        doc(s, "ACE Cranes bags large order for pick and carry cranes", image_url="https://lh3.googleusercontent.com/J6_coFbogxhRI9=s0-w300"),
    ])
    by_title = {a.title: a for a in rows(maker, sid)}
    assert by_title["JCB India inaugurates dealer head office in Vadodara"].image_url == "https://cdn.autocarpro.in/2026/09/hero.jpg"
    assert by_title["ACE Cranes bags large order for pick and carry cranes"].image_url is None


# ---- API ---------------------------------------------------------------------------------------------------------------
def test_articles_api_lists_only_accepted_with_relevance_and_filters_by_client(admin_client, db_engine) -> None:
    maker = async_sessionmaker(db_engine, expire_on_commit=False)
    run_pipeline(maker, lambda s: [
        doc(s, "JCB India expands manufacturing capacity at Pune plant"),
        doc(s, "Definedge Securities launches new wealth-tech platform for investors"),
        doc(s, "Generic stock market update: Nifty ends flat as banks drag benchmark index"),
    ])
    every = admin_client.get("/articles").json()
    assert sorted(a["title"] for a in every) == [
        "Definedge Securities launches new wealth-tech platform for investors",
        "JCB India expands manufacturing capacity at Pune plant",
    ]
    jcb = next(a for a in every if a["title"].startswith("JCB"))
    assert jcb["entity_names"] == ["JCB"] and jcb["relevance"]["entity"] == "JCB" and jcb["relevance"]["matched_terms"]
    only_jcb = admin_client.get("/articles", params={"entity_key": "jcb"}).json()
    assert [a["title"] for a in only_jcb] == ["JCB India expands manufacturing capacity at Pune plant"]
    assert admin_client.get("/articles", params={"entity_key": "ism"}).json() == []
    assert admin_client.get("/articles?resolve_images=false").status_code == 200
