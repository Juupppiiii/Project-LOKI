"""Tests for the rag/functions/document.py — ticket-to-Document conversion."""

from server.rag.functions.document import ticket_to_document


def test_ticket_to_document_happy_path():
    ticket = {
        "number": "INC001",
        "short_description": "Server unresponsive",
        "resolution_clean": "Rebooted the server and services recovered.",
        "assignment_group": "Wintel Support",
        "category": "Events-CMK",
    }
    doc = ticket_to_document(ticket)
    assert doc is not None
    assert "Server unresponsive" in doc.text
    assert "Rebooted" in doc.text
    assert doc.metadata["number"] == "INC001"
    assert doc.metadata["assignment_group"] == "Wintel Support"


def test_ticket_to_document_no_resolution():
    ticket = {"number": "INC002", "short_description": "Something broke"}
    doc = ticket_to_document(ticket)
    assert doc is None


def test_ticket_to_document_no_description():
    ticket = {"resolution_clean": "Fixed it"}
    doc = ticket_to_document(ticket)
    assert doc is None


def test_ticket_to_document_fallback_to_close_notes():
    ticket = {
        "number": "INC003",
        "short_description": "Disk full",
        "close_notes_raw": "Cleared /tmp and /var/log.",
    }
    doc = ticket_to_document(ticket)
    assert doc is not None
    assert "Cleared /tmp" in doc.text
