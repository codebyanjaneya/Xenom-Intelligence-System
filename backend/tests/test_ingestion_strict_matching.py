"""Test that ingestion uses strict entity matcher."""

import asyncio
import uuid
from datetime import datetime, timezone
from sqlalchemy import select

from app.db.session import init_engine, get_sessionmaker, dispose_engine
from app.models import Article, EntityMention, Entity
from app.adapters.ingestion.base import NormalizedDocument


def test_ingestion_rejects_false_positive_vikas():
    """Verify new articles ingested with strict matcher reject Vidya Vikas."""

    async def run_test():
        init_engine()
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            # Create a test source
            from app.models import Source

            source = Source(
                name="Test Source",
                source_type="RSS",
                canonical_url=f"https://test.example.com/{uuid.uuid4()}",
                tier="TIER_3",
            )
            session.add(source)
            await session.flush()

            # Create document simulating Vidya Vikas (should be rejected)
            doc = NormalizedDocument(
                source_id=source.id,
                canonical_url=f"https://example.com/article/{uuid.uuid4()}",
                title="Vidya Vikas hosts insightful panel on Civil & Infrastructure",
                published_at=datetime.now(timezone.utc),
                retrieved_at=datetime.now(timezone.utc),
                raw_content="<html>Content</html>",
                normalized_content="Vidya Vikas educational panel discussion",
                content_type="text/html",
                metadata={},
            )

            # Create article from document
            article = Article(
                source_id=source.id,
                url=doc.canonical_url,
                canonical_url=doc.canonical_url,
                title=doc.title,
                published_at=doc.published_at,
                fetched_at=doc.retrieved_at,
                raw_text=doc.raw_content,
                summary=doc.normalized_content,
                processing_status="parsed",
            )
            session.add(article)
            await session.flush()

            # Match entities using strict matcher
            from app.services.entity_matcher import match_entities_for_article
            entity_mentions = await match_entities_for_article(session, article)

            # Should have NO VIKAS entity mention (Vidya Vikas is excluded)
            vikas_mentions = [m for m in entity_mentions if "VIKAS" in str(m.entity_id)]
            assert len(vikas_mentions) == 0, f"Should reject Vidya Vikas, got {len(vikas_mentions)} mentions"

            await session.commit()

    asyncio.run(run_test())


def test_ingestion_accepts_true_positive_jcb():
    """Verify new articles ingested with strict matcher accept genuine JCB."""

    async def run_test():
        init_engine()
        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            # Create a test source
            from app.models import Source

            source = Source(
                name="Test Source",
                source_type="RSS",
                canonical_url=f"https://test.example.com/{uuid.uuid4()}",
                tier="TIER_3",
            )
            session.add(source)
            await session.flush()

            # Create document about JCB (should be accepted)
            doc = NormalizedDocument(
                source_id=source.id,
                canonical_url=f"https://example.com/article/{uuid.uuid4()}",
                title="JCB unveils hydrogen-powered excavator",
                published_at=datetime.now(timezone.utc),
                retrieved_at=datetime.now(timezone.utc),
                raw_content="<html>Content</html>",
                normalized_content="JCB construction equipment manufacturer announces excavator technology",
                content_type="text/html",
                metadata={},
            )

            # Create article from document
            article = Article(
                source_id=source.id,
                url=doc.canonical_url,
                canonical_url=doc.canonical_url,
                title=doc.title,
                published_at=doc.published_at,
                fetched_at=doc.retrieved_at,
                raw_text=doc.raw_content,
                summary=doc.normalized_content,
                processing_status="parsed",
            )
            session.add(article)
            await session.flush()

            # Match entities using strict matcher
            from app.services.entity_matcher import match_entities_for_article
            entity_mentions = await match_entities_for_article(session, article)

            # Should have JCB entity mention
            assert len(entity_mentions) > 0, "Should accept genuine JCB article"

            await session.commit()

    asyncio.run(run_test())


if __name__ == "__main__":
    test_ingestion_rejects_false_positive_vikas()
    test_ingestion_accepts_true_positive_jcb()
    print("✓ Ingestion integration tests passed")
