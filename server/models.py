"""Shared Pydantic models for the API request/response contract."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TicketQuery(BaseModel):
    """A new or incoming ticket to look up resolutions for."""

    short_description: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=5000)
    category: str = Field(default="")
    assignment_group: str = Field(default="")


class ResolvedTicket(BaseModel):
    """A historically resolved ticket retrieved by similarity."""

    number: str
    short_description: str
    assignment_group: str = ""
    resolution: str
    similarity: float = Field(ge=0.0, le=1.0)


class Recommendation(BaseModel):
    """Claude's synthesized resolution recommendation with citations."""

    summary: str
    steps: list[str] = Field(default_factory=list)
    cited_tickets: list[str] = Field(default_factory=list)
    confidence: str = ""


class QueryResponse(BaseModel):
    """The full response: retrieved tickets + AI recommendation."""

    query: TicketQuery
    similar_tickets: list[ResolvedTicket]
    recommendation: Recommendation
    model: str = ""
    retrieval_count: int = 0
