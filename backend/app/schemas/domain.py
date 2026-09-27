"""Cross-cutting intelligence views: technology themes, industry, competitors, client summaries."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class EvidenceQuote(BaseModel):
    field: str
    start: int
    end: int
    quote: str


class SignalRef(BaseModel):
    """One stored article, with the verbatim quote that justifies it appearing in a view."""
    article_id: uuid.UUID
    title: str | None
    url: str
    published_at: datetime | None
    source: str | None
    publisher: str | None
    clients: list[str]
    matched_terms: list[str]
    match_type: str = "literal_term_match"  # a stored word/phrase occurs; NOT a semantic classification
    evidence: EvidenceQuote


class ThemeResult(BaseModel):
    key: str
    label: str
    status: str  # EVIDENCE_FOUND | NO_EVIDENCE_IN_STORED_CORPUS
    article_count: int  # every stored accepted article with clean verbatim evidence (not just the ones listed)
    by_client: dict[str, int]
    signals: list[SignalRef]  # newest first, at most `per_theme`
    discovery: dict  # how this theme is sourced: configured phrase, queries, cadence, measured attempts


class ThemeView(BaseModel):
    scope: str
    method: str = "literal_term_match (whole words, case-insensitive); not a semantic classification"
    themes_with_evidence: list[str]  # theme keys
    themes_without_evidence: list[str]  # theme keys: unavailable, no stored evidence
    themes: list[ThemeResult]
    excluded_without_clean_evidence: int
    note: str


class IndustryTermResult(BaseModel):
    term: str
    article_count: int
    signals: list[SignalRef]


class ClientIndustry(BaseModel):
    client: str
    accepted_articles: int
    industry_terms: list[IndustryTermResult]


class IndustryEntityResult(BaseModel):
    industry: str  # a registered INDUSTRY entity
    status: str  # EVIDENCE_FOUND | NO_EVIDENCE_IN_STORED_CORPUS
    article_count: int
    by_client: dict[str, int]
    signals: list[SignalRef]


class IndustryView(BaseModel):
    scope: str
    method: str = "literal_term_match (whole words, case-insensitive); not a semantic classification"
    clients: list[ClientIndustry]
    registered_industries: list[str]
    industry_entities: list[IndustryEntityResult]  # each registered industry resolved to stored articles by literal name match
    taxonomy_labels: dict[str, int]  # stored article.topic labels (keyword classifier), accepted articles
    topic_coverage: dict  # how complete the stored labels are, measured against the current deterministic classifier
    note: str


class ResearchRecord(BaseModel):
    kind: str = "paper"  # paper | model — which adapter this came from (openalex/arxiv vs huggingface)
    title: str | None
    url: str  # canonical publisher URL
    published_at: datetime | None
    publisher: str | None
    authors: list[str]  # only what is stored
    authors_status: str  # NOT_STORED until an author source exists
    institutions: list[str]
    institutions_status: str
    evidence: EvidenceQuote
    metrics: dict = {}  # real, source-stated only (HF downloads/likes, OpenAlex cited_by_count) — never computed


class ResearchView(BaseModel):
    status: str  # RESEARCH_RECORDS_FOUND | NO_RESEARCH_DATA_IN_STORED_CORPUS
    records: list[ResearchRecord]
    papers: list[ResearchRecord] = []
    models: list[ResearchRecord] = []
    reason: str
    interface: dict  # the fields a research source must provide to appear here


class CompetitorView(BaseModel):
    configured: bool
    competitors: list[dict]
    unverified_competitor_language_articles: int
    reason: str
    how_to_configure: str


class ClientSummary(BaseModel):
    client: str
    accepted_articles: int
    latest_published_at: datetime | None
    publishers: int
    source_classes: dict[str, int]
    topic_labels: dict[str, int]


class ClientSummaryView(BaseModel):
    clients: list[ClientSummary]
    total_accepted: int
    total_rejected: int  # rejected rows carry no verified client, so they are not split per client


class DigestItem(BaseModel):
    """One 'worth your attention' item: always a real stored article/researcher document with its evidence."""
    kind: str  # article | researcher_work
    title: str | None
    url: str
    published_at: datetime | None
    source: str | None
    theme: str | None  # theme key this came from, when kind == article
    evidence: EvidenceQuote | None


class DigestSection(BaseModel):
    key: str
    label: str
    status: str  # EVIDENCE_FOUND | NO_EVIDENCE_IN_STORED_CORPUS
    article_count: int
    signals: list[SignalRef]


class MorningDigest(BaseModel):
    """A saved query over the existing search/index (PDF s.11, s.15): embodied AI, robotics, creative AI, AI
    models/Hugging Face, plus global researchers/labs and 3-5 'worth your attention' items. Deterministic:
    built entirely from stored, evidenced signals, never from a generated summary."""
    generated_at: datetime
    sections: list[DigestSection]
    researchers: list[dict]  # recent researcher work, from the existing researcher system
    worth_your_attention: list[DigestItem]
    note: str
