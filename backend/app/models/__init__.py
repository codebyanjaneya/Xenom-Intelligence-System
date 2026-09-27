"""Import all models so they register on Base.metadata (used by Alembic)."""

from __future__ import annotations

from app.db.base import Base
from app.models.architecture import (
    ArticleLabel,
    ClientProfile,
    DynamicClient,
    ClientSource,
    ConfigAudit,
    DocumentAuthor,
    DocumentChunk,
    EvaluationLabel,
    EvaluationRun,
    EvaluationSet,
    FineTuneDataset,
    FineTuneJob,
    ProfileSuggestion,
    Recommendation,
    SavedArticle,
)
from app.models.content import Article, ArticleEventLink, Event, Thread
from app.models.delivery import Alert, Briefing
from app.models.entities import (
    Entity,
    EntityAlias,
    EntityMention,
    Relationship,
    RelationshipEvidence,
)
from app.models.evidence import (
    Claim,
    ClaimGroup,
    ClaimGroupMember,
    ClaimSource,
    Contradiction,
)
from app.models.identity import AlertRead, User, UserNote, UserPreference
from app.models.intelligence import AiUsage, Insight, InsightClaim
from app.models.review import ReviewItem
from app.models.capability import Opportunity, XenomCapability
from app.models.sources import Source, SourceHealth

__all__ = [
    "ArticleLabel", "ClientProfile", "DynamicClient", "ClientSource", "ConfigAudit", "DocumentAuthor", "DocumentChunk",
    "EvaluationLabel", "EvaluationRun", "EvaluationSet", "FineTuneDataset", "FineTuneJob", "ProfileSuggestion", "Recommendation", "SavedArticle",
    "Base",
    "Article",
    "ArticleEventLink",
    "Event",
    "Thread",
    "Alert",
    "Briefing",
    "Entity",
    "EntityAlias",
    "EntityMention",
    "Relationship",
    "RelationshipEvidence",
    "Claim",
    "ClaimGroup",
    "ClaimGroupMember",
    "ClaimSource",
    "Contradiction",
    "AlertRead",
    "User",
    "UserNote",
    "UserPreference",
    "AiUsage",
    "Insight",
    "InsightClaim",
    "ReviewItem",
    "Opportunity",
    "XenomCapability",
    "Source",
    "SourceHealth",
]
