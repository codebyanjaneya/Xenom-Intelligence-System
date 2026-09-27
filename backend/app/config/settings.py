"""Environment-based application settings (M0).

Values come from environment variables / a local .env file. No secrets are
hardcoded. Only application metadata is needed at M0; database and provider
settings are added in later milestones.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[3] / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "xenom-intel-api"
    app_version: str = "0.1.0"
    environment: str = "development"
    log_level: str = "INFO"
    dev_user_password: str | None = None
    # Provider behind POST /search only ("brave" or "fixture"). The default never serves fixture data: an unset or
    # unknown value means "unconfigured" and POST /search answers 503. The stored-article engine does not use it.
    search_provider: str = "unconfigured"
    brave_api_key: str | None = None
    openalex_api_key: str | None = None
    semantic_scholar_api_key: str | None = None

    # Async SQLAlchemy URL. Overridden by env/.env (compose points host to
    # the "postgres" service). No credentials are hardcoded in source.
    database_url: str = "postgresql+asyncpg://xenom:change_me_local@localhost:5433/xenom_intel"

    # JWT / auth. The default secret is an obvious dev placeholder and MUST be
    # overridden via environment in any non-local deployment.
    jwt_secret: str = "dev-insecure-secret-change-me-please-32-bytes-min"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_issuer: str = "xenom-intel"
    jwt_audience: str = "xenom-intel-api"

    # Embeddings (PDF s.10; provider is an open decision, s.16). "none" keeps everything lexical and sends nothing anywhere.
    # "local" = sentence-transformers in-process; "openai_compatible" = an /embeddings endpoint (opt-in: text leaves the environment).
    embedding_provider: str = "none"
    embedding_model: str | None = None
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None

    # LLM enrichment (PDF s.10; provider/budget are open decisions, s.16). "none" disables every LLM operation.
    # "anthropic" = Messages API; "openai_compatible" = /chat/completions endpoint; "groq" = Groq's OpenAI-compatible
    # /chat/completions endpoint (reuses OpenAIChatProvider, no separate LLM system). Prompts/models are versioned in code.
    llm_provider: str = "none"
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_max_output_tokens: int = 1024
    llm_input_cost_per_1k_usd: float = 0.0
    llm_output_cost_per_1k_usd: float = 0.0
    llm_daily_call_budget: int = 200

    # Groq (LLM_PROVIDER=groq): a Groq-issued API key and model name. Never used unless LLM_PROVIDER=groq.
    groq_api_key: str | None = None
    groq_model: str | None = None

    # Hybrid retrieval (PDF s.8): reciprocal rank fusion weights and the optional re-rank (0 = off).
    retrieval_rrf_k: int = 60
    retrieval_weight_lexical: float = 1.0
    retrieval_weight_vector: float = 1.0
    rerank_weight_client: float = 0.0
    rerank_weight_reliability: float = 0.0
    rerank_weight_recency: float = 0.0
    recall_similarity_threshold: float = 0.55


class ProductionConfigError(RuntimeError):
    pass


def validate_for_production(settings: Settings) -> None:
    """Refuse to start in production with development defaults or test-only providers."""
    if settings.environment.strip().lower() != "production":
        return
    problems = []
    if settings.jwt_secret.startswith("dev-insecure") or len(settings.jwt_secret) < 32:
        problems.append("JWT_SECRET is the development placeholder or too short")
    if "change_me" in settings.database_url:
        problems.append("DATABASE_URL still uses the development password")
    if settings.search_provider.strip().lower() == "fixture":
        problems.append("SEARCH_PROVIDER=fixture (test-only provider) is not allowed")
    if settings.embedding_provider.strip().lower() not in ("none", "local", "openai_compatible"):
        problems.append("EMBEDDING_PROVIDER must be none, local or openai_compatible")
    if settings.llm_provider.strip().lower() not in ("none", "anthropic", "openai_compatible", "groq"):
        problems.append("LLM_PROVIDER must be none, anthropic, openai_compatible or groq")
    if settings.llm_provider.strip().lower() == "anthropic" and not settings.llm_api_key:
        problems.append("LLM_PROVIDER=anthropic needs LLM_API_KEY")
    if settings.llm_provider.strip().lower() == "groq" and not settings.groq_api_key:
        problems.append("LLM_PROVIDER=groq needs GROQ_API_KEY")
    if problems:
        raise ProductionConfigError("Unsafe production configuration: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor (single instance per process)."""
    return Settings()
