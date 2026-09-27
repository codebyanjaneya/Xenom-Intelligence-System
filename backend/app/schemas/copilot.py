from __future__ import annotations

from pydantic import BaseModel, Field


class CopilotRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    history: list["CopilotTurn"] = Field(default_factory=list, max_length=8)


class CopilotTurn(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    answer: str = Field(default="", max_length=3000)
    intent: str = Field(default="", max_length=60)
    client: str | None = Field(default=None, max_length=100)


class CopilotCitation(BaseModel):
    title: str
    url: str
    date: str | None = None
    quote: str | None = None


class CopilotAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=3000)
    insufficient_evidence: bool = False
    citations: list[CopilotCitation] = Field(default_factory=list, max_length=12)


class CopilotResponse(BaseModel):
    query: str
    intent: str
    client: str | None = None
    answer: str
    insufficient_evidence: bool
    citations: list[CopilotCitation]
    data: dict = Field(default_factory=dict)