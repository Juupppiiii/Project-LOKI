"""Tests for the advisory LLM-assisted step recommender and its retrieval.

The guarantees under test are mostly safety boundaries:

* recommend_steps creates no run, approves nothing, executes nothing;
* it never changes the authoritative keyword match;
* grounding (procedures + solved tickets) is real and inspectable;
* offline (no live model) it degrades honestly: model_available is False and it
  says so, rather than fabricating a plan;
* a (fake) live backend is used only when explicitly enabled, and its output is
  carried through as advisory text with the real grounding attached.
"""

from __future__ import annotations

import pytest

from overlay import knowledge_base as kb
from overlay import knowledge_llm as kllm

pytestmark = pytest.mark.unit


class FakeBackend:
    """A stand-in live backend: records the prompt, returns canned advice."""

    name = "fake-live-model"

    def __init__(self):
        self.last_system = None
        self.last_user = None

    def complete(self, system, user, *, max_tokens=1200):
        self.last_system = system
        self.last_user = user
        return "Recommended: step 1 ..., citing INC-xyz. Destructive steps human-only."


# ---------------------------------------------------------------------------
# Backend seam
# ---------------------------------------------------------------------------


def test_default_backend_is_offline_echo(monkeypatch):
    monkeypatch.delenv("LOKI_LLM_ENABLED", raising=False)
    backend = kllm.make_backend()
    assert isinstance(backend, kllm.EchoBackend)
    assert "offline" in backend.name


def test_echo_backend_does_not_fabricate_a_fix():
    text = kllm.EchoBackend().complete("sys", "user")
    assert "OFFLINE ADVISORY" in text
    # It must not invent steps; it only says a live model is needed.
    assert "no live model" in text.lower()


def test_bedrock_backend_raises_unavailable_without_boto(monkeypatch):
    # With no client injected and boto3 import failing, it must raise cleanly.
    backend = kllm.BedrockBackend(client=None)
    monkeypatch.setitem(__import__("sys").modules, "boto3", None)
    with pytest.raises(kllm.LLMUnavailable):
        backend.complete("s", "u")


# ---------------------------------------------------------------------------
# Retrieval grounding
# ---------------------------------------------------------------------------


def test_solved_index_retrieves_similar_real_tickets():
    idx = kb.shared_solved_index()
    if idx.size == 0:
        pytest.skip("no closed_incidents.csv available in this checkout")
    hits = idx.retrieve({"short_description": "Spot Recalled instance", "description": "terminate ec2"}, top=3)
    assert hits
    # Each hit carries a real ticket number and a resolution string.
    for h in hits:
        assert h.number
        assert h.resolution


# ---------------------------------------------------------------------------
# Service: recommend_steps is advisory and grounded
# ---------------------------------------------------------------------------


def test_recommend_steps_creates_no_run(service, restart_ticket):
    before = len(service.list_runs())
    result = service.recommend_steps(restart_ticket)
    assert result["advisory"] is True
    assert len(service.list_runs()) == before  # nothing proposed


def test_recommend_steps_keeps_authoritative_match(service, restart_ticket):
    result = service.recommend_steps(restart_ticket)
    # The authoritative pick is the keyword match, not a model decision.
    assert result["authoritative_match"] == "wintel.service_restart"


def test_recommend_steps_offline_is_honest(service, restart_ticket, monkeypatch):
    monkeypatch.delenv("LOKI_LLM_ENABLED", raising=False)
    result = service.recommend_steps(restart_ticket)
    assert result["model_available"] is False
    assert "offline" in result["backend"]
    # Grounding is still real.
    assert result["grounding"]["source_counts"]["candidate_procedures"] >= 1


def test_recommend_steps_uses_live_backend_when_present(service, restart_ticket, monkeypatch):
    fake = FakeBackend()
    monkeypatch.setattr(kllm, "make_backend", lambda **kw: fake)
    result = service.recommend_steps(restart_ticket)
    assert result["model_available"] is True
    assert result["backend"] == "fake-live-model"
    assert "citing" in result["recommendation"].lower()
    # The prompt the model saw was grounded in the matched procedure.
    assert "wintel.service_restart" in fake.last_user or "Restart a Windows service" in fake.last_user


def test_recommend_steps_grounding_includes_solved_tickets(service, restart_ticket):
    result = service.recommend_steps(restart_ticket)
    counts = result["grounding"]["source_counts"]
    assert "solved_tickets" in counts
    assert "solved_index_size" in counts
