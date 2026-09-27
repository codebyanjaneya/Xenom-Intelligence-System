"""Groq provider integration: reuses OpenAIChatProvider (Groq's endpoint is OpenAI-compatible) via LLM_PROVIDER=groq.
No real Groq credentials are used or required; the provider selection and capability reporting are tested directly.
"""
from __future__ import annotations

from app.services import llm


def test_groq_is_not_selected_without_an_api_key(monkeypatch) -> None:
    s = llm.get_settings()
    monkeypatch.setattr(s, "llm_provider", "groq")
    monkeypatch.setattr(s, "groq_api_key", None)
    assert llm.get_provider() is None
    assert llm.capability()["status"] == "not_configured"


def test_groq_is_selected_and_reuses_the_openai_compatible_client(monkeypatch) -> None:
    s = llm.get_settings()
    monkeypatch.setattr(s, "llm_provider", "groq")
    monkeypatch.setattr(s, "groq_api_key", "test-key-not-real")
    monkeypatch.setattr(s, "groq_model", "llama-3.3-70b-versatile")
    provider = llm.get_provider()
    assert isinstance(provider, llm.OpenAIChatProvider)  # no separate client class
    assert provider.name == "groq" and provider.model == "llama-3.3-70b-versatile"
    assert provider.base_url == llm.GROQ_BASE_URL.rstrip("/")
    assert provider.api_key == "test-key-not-real"


def test_groq_defaults_to_a_model_when_none_is_configured(monkeypatch) -> None:
    s = llm.get_settings()
    monkeypatch.setattr(s, "llm_provider", "groq")
    monkeypatch.setattr(s, "groq_api_key", "test-key-not-real")
    monkeypatch.setattr(s, "groq_model", None)
    provider = llm.get_provider()
    assert provider.model == llm.GROQ_DEFAULT_MODEL


def test_capability_reports_groq_without_leaking_the_key(monkeypatch) -> None:
    s = llm.get_settings()
    monkeypatch.setattr(s, "llm_provider", "groq")
    monkeypatch.setattr(s, "groq_api_key", "super-secret-key")
    monkeypatch.setattr(s, "groq_model", "llama-3.3-70b-versatile")
    cap = llm.capability()
    assert cap["status"] == "configured" and cap["provider"] == "groq" and cap["model"] == "llama-3.3-70b-versatile"
    assert "super-secret-key" not in str(cap)


def test_capabilities_endpoint_reflects_groq_configuration(admin_client, monkeypatch) -> None:
    s = llm.get_settings()
    monkeypatch.setattr(s, "llm_provider", "groq")
    monkeypatch.setattr(s, "groq_api_key", "test-key-not-real")
    monkeypatch.setattr(s, "groq_model", "llama-3.3-70b-versatile")
    caps = admin_client.get("/engine/capabilities").json()
    assert caps["llm"]["status"] == "configured" and caps["llm"]["provider"] == "groq"
    assert "test-key-not-real" not in str(caps)
