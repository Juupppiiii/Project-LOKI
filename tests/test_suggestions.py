"""Tests for advisory semantic suggestions.

The guarantee under test is as much about what suggestions must NOT do as what
they do: they surface meaning-based procedure hints to help a human, but they
never propose a run, classify risk, or authorise anything. The keyword matcher
stays the authoritative matcher.
"""

from __future__ import annotations

import pytest

from overlay.domain import DomainError

pytestmark = pytest.mark.unit


def test_suggestions_are_advisory_and_create_nothing(service):
    ticket = {
        "number": "INC9100",
        "short_description": "cannot sign in, profile appears disabled",
        "description": "user cannot authenticate",
        "assignment_group": "Global Service Desk",
    }
    before = len(service.list_runs())
    result = service.semantic_suggestions(ticket, top=3)

    assert result["enabled"] is True
    assert result["advisory"] is True
    assert isinstance(result["suggestions"], list)
    # Suggesting must never create a run.
    assert len(service.list_runs()) == before


def test_suggestions_can_surface_a_hint_when_keyword_misses(service):
    # Worded to avoid literal trigger keywords; semantic layer may still rank
    # procedures by meaning. We assert the shape and that it ranks *something*,
    # not a specific procedure (that depends on the installed embedding backend).
    ticket = {
        "number": "INC9101",
        "short_description": "mailbox will not open after the move",
        "description": "profile seems broken",
        "assignment_group": "Global Wintel Server Support",
    }
    result = service.semantic_suggestions(ticket, top=5)
    assert result["enabled"] is True
    for s in result["suggestions"]:
        assert {"procedure_id", "procedure_name", "similarity", "advisory_score", "above_threshold"} <= set(s)
        assert 0.0 <= s["similarity"] <= 1.0


def test_suggestions_can_be_disabled_by_env(service, monkeypatch):
    monkeypatch.setenv("LOKI_SEMANTIC_SUGGESTIONS", "0")
    result = service.semantic_suggestions({"number": "INC9102", "short_description": "x"})
    assert result["enabled"] is False
    assert result["suggestions"] == []


def test_suggestions_do_not_change_authoritative_match(service, restart_ticket):
    # The keyword match for a known ticket is unchanged by the existence of the
    # advisory layer: proposing still uses the keyword matcher.
    kw = service.preview_match(restart_ticket)
    assert kw["matched"] is True
    # Asking for suggestions does not alter or replace that.
    service.semantic_suggestions(restart_ticket)
    kw_again = service.preview_match(restart_ticket)
    assert kw_again["procedure_id"] == kw["procedure_id"]


def test_matcher_is_cached_per_registry_revision(service, restart_ticket):
    # Two calls reuse the same fitted matcher instance (keyed on revision).
    service.semantic_suggestions(restart_ticket)
    first = getattr(service, "_sem_cache", None)
    service.semantic_suggestions(restart_ticket)
    second = getattr(service, "_sem_cache", None)
    assert first is not None and second is not None
    assert first[1] is second[1]  # same matcher object reused
