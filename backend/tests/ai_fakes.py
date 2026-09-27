"""TEST-ONLY providers used to exercise the embedding / LLM plumbing (validation, fallback, logging, budgets).

They are injected with ``set_provider`` inside tests and are never reachable from configuration: production settings only
know ``none`` / ``local`` / ``openai_compatible`` (embeddings) and ``none`` / ``anthropic`` / ``openai_compatible`` (LLM).
``ConceptEmbeddings`` maps words of the same concept group onto the same vector dimension, which is what lets a test show a
semantic hit that shares no literal word with the query. It makes no claim about real embedding quality.
"""
from __future__ import annotations

import hashlib
import json
import re

from app.services.embeddings import EmbeddingUnavailable
from app.services.llm import LLMResult

CONCEPTS = [
    {"excavator", "digger", "earthmover", "backhoe"},
    {"semiconductor", "chip", "chips", "fab", "wafer"},
    {"loan", "credit", "lending"},
]
DIM = 48


class ConceptEmbeddings:
    name = "test"

    def __init__(self, model: str = "concept-v1", fail: bool = False):
        self.model, self.fail, self.calls = model, fail, 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self.fail:
            raise EmbeddingUnavailable("test provider is down")
        out = []
        for text in texts:
            vec = [0.0] * DIM
            for word in re.findall(r"[a-z0-9]+", text.lower()):
                group = next((i for i, g in enumerate(CONCEPTS) if word in g), None)
                slot = group if group is not None else 8 + int(hashlib.md5(word.encode()).hexdigest(), 16) % (DIM - 8)
                vec[slot] += 1.0
            norm = sum(x * x for x in vec) ** 0.5 or 1.0
            out.append([x / norm for x in vec])
        return out


class ScriptedLLM:
    """Returns the queued answers in order; records every call."""

    name = "test"

    def __init__(self, *answers: object, model: str = "scripted-1", fail: bool = False):
        self.model, self.answers, self.fail, self.calls = model, list(answers), fail, []

    async def complete(self, system: str, user: str, *, max_tokens: int) -> LLMResult:
        self.calls.append((system, user))
        if self.fail:
            raise RuntimeError("boom")
        answer = self.answers.pop(0)
        return LLMResult(answer if isinstance(answer, str) else json.dumps(answer), 120, 40)
