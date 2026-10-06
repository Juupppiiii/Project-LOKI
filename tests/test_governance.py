"""Tests for the governance controls: execution mode, separation of duties,
and approval expiry. All run against isolated SQLite in simulation mode.
"""

from __future__ import annotations

import pytest

from overlay.domain import DomainError, RunState, StepState

pytestmark = pytest.mark.integration


def _agent_chunk(run):
    return next(c for c in run.chunks if c.owner.value == "ai")


# ---------------------------------------------------------------------------
# Execution mode threading (gaps #1/#2)
# ---------------------------------------------------------------------------


def test_default_run_is_simulation_mode(service, restart_ticket):
    run = service.propose(restart_ticket, actor="alice")
    assert run.mode == "simulation"


def test_live_mode_run_is_labelled_live(service, restart_ticket):
    run = service.propose(restart_ticket, actor="alice", mode="live")
    assert run.mode == "live"
    # The audit records the mode.
    proposed = next(e for e in service.audit(run.run_id) if e["event"] == "run.proposed")
    assert proposed["detail"]["mode"] == "live"


def test_invalid_mode_is_refused(service, restart_ticket):
    with pytest.raises(DomainError):
        service.propose(restart_ticket, actor="alice", mode="yolo")


def test_simulation_run_cannot_execute_against_live_only(service, restart_ticket):
    # A simulation-mode run executes fine on the default simulation gateway.
    run = service.propose(restart_ticket, actor="alice", mode="simulation")
    chunk = _agent_chunk(run)
    service.decide_chunk(run.run_id, chunk.chunk_id, decision="approved", actor="bob")
    service.execute_chunk(run.run_id, chunk.chunk_id, actor="agent")
    final = service.get_run(run.run_id)
    assert any(s.state is StepState.SUCCEEDED for s in final.steps)


# ---------------------------------------------------------------------------
# Separation of duties (gap #3)
# ---------------------------------------------------------------------------


def test_same_actor_cannot_approve_and_trigger(service, restart_ticket, monkeypatch):
    monkeypatch.setenv("LOKI_ENFORCE_SOD", "1")
    run = service.propose(restart_ticket, actor="alice")
    chunk = _agent_chunk(run)
    service.decide_chunk(run.run_id, chunk.chunk_id, decision="approved", actor="carol")
    # Same person who approved tries to trigger -> refused.
    with pytest.raises(DomainError):
        service.execute_chunk(run.run_id, chunk.chunk_id, actor="carol")
    events = [e["event"] for e in service.audit(run.run_id)]
    assert "execution.refused" in events


def test_different_actor_can_trigger(service, restart_ticket, monkeypatch):
    monkeypatch.setenv("LOKI_ENFORCE_SOD", "1")
    run = service.propose(restart_ticket, actor="alice")
    chunk = _agent_chunk(run)
    service.decide_chunk(run.run_id, chunk.chunk_id, decision="approved", actor="carol")
    service.execute_chunk(run.run_id, chunk.chunk_id, actor="dave")  # different -> ok
    final = service.get_run(run.run_id)
    assert any(s.state is StepState.SUCCEEDED for s in final.steps)


def test_sod_can_be_disabled(service, restart_ticket, monkeypatch):
    monkeypatch.setenv("LOKI_ENFORCE_SOD", "0")
    run = service.propose(restart_ticket, actor="alice")
    chunk = _agent_chunk(run)
    service.decide_chunk(run.run_id, chunk.chunk_id, decision="approved", actor="carol")
    # Same actor allowed when SoD is off (single-user dev box).
    service.execute_chunk(run.run_id, chunk.chunk_id, actor="carol")
    final = service.get_run(run.run_id)
    assert any(s.state is StepState.SUCCEEDED for s in final.steps)


# ---------------------------------------------------------------------------
# Approval expiry (gap #4)
# ---------------------------------------------------------------------------


def test_expired_approval_is_refused(service, restart_ticket, monkeypatch):
    monkeypatch.setenv("LOKI_APPROVAL_TTL_SECONDS", "3600")
    monkeypatch.setenv("LOKI_ENFORCE_SOD", "0")
    run = service.propose(restart_ticket, actor="alice")
    chunk = _agent_chunk(run)
    service.decide_chunk(run.run_id, chunk.chunk_id, decision="approved", actor="alice")

    # Make the approval look old. created_at is deliberately immutable in the
    # store, so we age the clock instead: patch the service's age helper to
    # report the approval as older than the TTL.
    import overlay.service as service_module
    monkeypatch.setattr(service_module, "_age_seconds", lambda _ts: 10_000.0)

    with pytest.raises(DomainError):
        service.execute_chunk(run.run_id, chunk.chunk_id, actor="alice")
    events = [e["event"] for e in service.audit(run.run_id)]
    assert "execution.refused" in events


def test_fresh_approval_within_ttl_executes(service, restart_ticket, monkeypatch):
    monkeypatch.setenv("LOKI_APPROVAL_TTL_SECONDS", "3600")
    monkeypatch.setenv("LOKI_ENFORCE_SOD", "0")
    run = service.propose(restart_ticket, actor="alice")
    chunk = _agent_chunk(run)
    service.decide_chunk(run.run_id, chunk.chunk_id, decision="approved", actor="alice")
    service.execute_chunk(run.run_id, chunk.chunk_id, actor="alice")
    final = service.get_run(run.run_id)
    assert any(s.state is StepState.SUCCEEDED for s in final.steps)
