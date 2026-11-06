"""Tests for the Pydantic models."""

from bucky.models import (
    QueryResponse,
    Recommendation,
    ResolvedTicket,
    TicketQuery,
)


def test_ticket_query_minimal():
    q = TicketQuery(short_description="Disk full on server X")
    assert q.short_description == "Disk full on server X"
    assert q.description == ""
    assert q.category == ""
    assert q.assignment_group == ""


def test_ticket_query_full():
    q = TicketQuery(
        short_description="CPU high",
        description="CPU at 99% for 10 minutes",
        category="Events-CMK",
        assignment_group="Global Wintel Server Support",
    )
    assert q.category == "Events-CMK"


def test_resolved_ticket():
    t = ResolvedTicket(
        number="INC123",
        short_description="Disk cleanup",
        resolution="Cleared temp files",
        similarity=0.85,
    )
    assert t.similarity == 0.85


def test_recommendation():
    r = Recommendation(
        summary="Clear the temp directory",
        cited_tickets=["INC123", "INC456"],
        confidence="high",
    )
    assert r.confidence == "high"
    assert len(r.cited_tickets) == 2


def test_query_response():
    resp = QueryResponse(
        query=TicketQuery(short_description="test"),
        similar_tickets=[],
        recommendation=Recommendation(summary="No data", confidence="insufficient"),
        model="test-model",
        retrieval_count=0,
    )
    assert resp.retrieval_count == 0
