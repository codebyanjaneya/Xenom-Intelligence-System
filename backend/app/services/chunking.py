"""Chunks with character offsets (PDF s.8): roughly 200-400 tokens with overlap, so a hit maps back to the exact source text.

Offsets index the stored field string exactly (``field[char_start:char_end] == chunk text``); markup is stripped only when text
is displayed or embedded. Title and summary are chunked separately from the body so they can be embedded separately.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.architecture import DocumentChunk
from app.models.content import Article

TARGET_WORDS = 250   # ~330 tokens
OVERLAP_WORDS = 40
MIN_WORDS_FOR_SPLIT = 350  # bodies up to this many words stay one chunk
_WORD = re.compile(r"\S+")


@dataclass(frozen=True)
class ChunkSpan:
    field: str
    index: int
    start: int
    end: int
    text: str


def make_chunks(field: str, value: str | None) -> list[ChunkSpan]:
    if not value or not value.strip():
        return []
    words = [(m.start(), m.end()) for m in _WORD.finditer(value)]
    if field == "title" or len(words) <= MIN_WORDS_FOR_SPLIT:
        start, end = words[0][0], words[-1][1]
        return [ChunkSpan(field, 0, start, end, value[start:end])]
    chunks: list[ChunkSpan] = []
    position, index = 0, 0
    while position < len(words):
        stop = min(position + TARGET_WORDS, len(words))
        start, end = words[position][0], words[stop - 1][1]
        chunks.append(ChunkSpan(field, index, start, end, value[start:end]))
        if stop == len(words):
            break
        position, index = stop - OVERLAP_WORDS, index + 1
    return chunks


def article_chunks(article: Article) -> list[ChunkSpan]:
    spans = make_chunks("title", article.title) + make_chunks("summary", article.summary)
    if article.raw_text and article.raw_text.strip() and article.raw_text != article.summary:
        spans += make_chunks("raw_text", article.raw_text)
    return spans


async def chunk_article(session: AsyncSession, article: Article) -> int:
    """(Re)write the chunk rows of one article. Existing vectors are dropped with their rows (re-embedded later)."""
    await session.execute(delete(DocumentChunk).where(DocumentChunk.article_id == article.id))
    spans = article_chunks(article)
    for span in spans:
        session.add(DocumentChunk(article_id=article.id, field=span.field, chunk_index=span.index, char_start=span.start,
                                  char_end=span.end, text_=span.text))
    await session.flush()
    return len(spans)


async def backfill_chunks(session: AsyncSession, *, limit: int = 5000) -> dict:
    """Explicit, admin-invoked: chunk accepted articles that have no chunks yet."""
    rows = (await session.scalars(
        select(Article).where(Article.processing_status == "accepted",
                              ~select(DocumentChunk.id).where(DocumentChunk.article_id == Article.id).exists())
        .order_by(Article.id).limit(limit))).all()
    total = 0
    for article in rows:
        total += await chunk_article(session, article)
    await session.commit()
    return {"articles_chunked": len(rows), "chunks": total}


async def chunk_stats(session: AsyncSession) -> dict:
    row = (await session.execute(text(
        "select count(*), count(embedding), count(distinct article_id), count(distinct embedding_model) filter (where embedding is not null) "
        "from document_chunks"))).one()
    return {"chunks": row[0], "embedded": row[1], "articles": row[2], "models": row[3]}
