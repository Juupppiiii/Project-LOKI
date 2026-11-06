"""Tests for the RAG engine — document conversion and confidence extraction."""

from bucky.rag import _extract_confidence, _ticket_to_document


def test_ticket_to_document_happy_path():
    ticket = {
        "number": "INC001",
        "short_description": "Server unresponsive",
        "resolution_clean": "Rebooted the server and services recovered.",
        "assignment_group": "Wintel Support",
        "category": "Events-CMK",
    }
    doc = _ticket_to_document(ticket)
    assert doc is not None
    assert "Server unresponsive" in doc.text
    assert "Rebooted" in doc.text
    assert doc.metadata["number"] == "INC001"
    assert doc.metadata["assignment_group"] == "Wintel Support"


def test_ticket_to_document_no_resolution():
    ticket = {"number": "INC002", "short_description": "Something broke"}
    doc = _ticket_to_document(ticket)
    assert doc is None  # no resolution -> skip


def test_ticket_to_document_no_description():
    ticket = {"resolution_clean": "Fixed it"}
    doc = _ticket_to_document(ticket)
    assert doc is None  # no short_description -> skip


def test_ticket_to_document_fallback_to_close_notes():
    ticket = {
        "number": "INC003",
        "short_description": "Disk full",
        "close_notes_raw": "Cleared /tmp and /var/log.",
    }
    doc = _ticket_to_document(ticket)
    assert doc is not None
    assert "Cleared /tmp" in doc.text


def test_extract_confidence():
    assert _extract_confidence("High confidence: this matches well") == "high"
    assert _extract_confidence("Confidence: medium based on 3 tickets") == "medium"
    assert _extract_confidence("Low confidence, only one match") == "low"
    assert _extract_confidence("Insufficient evidence to recommend") == "insufficient"
    assert _extract_confidence("Here is a recommendation.") == "medium"  # default
