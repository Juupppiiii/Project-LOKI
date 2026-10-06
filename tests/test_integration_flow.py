"""End-to-end integration: propose -> approve -> execute -> audit.

Runs the real OverlayService against isolated SQLite in simulation mode.
"""

from __future__ import annotations

import pytest

from overlay.domain import DomainError, RunState, StepState

pytestmark = pytest.mark.integration


def _agent_chunks(run):
    return [c for c in run.chunks if c.owner.value == "ai"]


def test_full_agent_run_completes(service, restart_ticket):
    run = service.propose(restart_ticket, actor="alice")
    assert run.procedure_name == "Restart a Windows service"
    assert run.state is RunState.AWAITING_APPROVAL

    for chunk in _agent_chunks(run):
        service.decide_chunk(run.run_id, chunk.chunk_id, decision="approved", actor="bob")
        service.execute_chunk(run.run_id, chunk.chunk_id, actor="agent")

    final = service.get_run(run.run_id)
    assert final.state is RunState.COMPLETED
    assert all(s.state is StepState.SUCCEEDED for s in final.steps)
    assert all(s.outcome.startswith("[SIM]") for s in final.steps)


def test_execution_without_approval_is_refused_and_audited(service, restart_ticket):
    run = service.propose(restart_ticket, actor="alice")
    chunk = _agent_chunks(run)[0]

    with pytest.raises(DomainError):
        service.execute_chunk(run.run_id, chunk.chunk_id, actor="agent")

    events = [e["event"] for e in service.audit(run.run_id)]
    assert "execution.refused" in events


def test_audit_records_agent_and_approver_separately(service, restart_ticket):
    run = service.propose(restart_ticket, actor="alice")
    chunk = _agent_chunks(run)[0]
    service.decide_chunk(run.run_id, chunk.chunk_id, decision="approved", actor="bob")
    service.execute_chunk(run.run_id, chunk.chunk_id, actor="agent")

    audit = service.audit(run.run_id)
    approved = [e for e in audit if e["event"] == "chunk.approved"]
    executed = [e for e in audit if e["event"] == "step.executed"]
    assert approved and approved[0]["actor"] == "bob"
    assert executed and executed[0]["actor"] == "agent"
    assert executed[0]["detail"].get("triggered_by") == "agent"
    assert executed[0]["detail"].get("approved_by") == "bob"


def test_editing_parameters_invalidates_prior_approval(service, restart_ticket):
    run = service.propose(restart_ticket, actor="alice")
    chunk = _agent_chunks(run)[0]
    service.decide_chunk(run.run_id, chunk.chunk_id, decision="approved", actor="bob")

    # Change a parameter the digest binds to; the old approval must no longer authorise.
    service.set_parameters(run.run_id, {"service_name": "SomethingElse"}, actor="alice")
    with pytest.raises(DomainError):
        service.execute_chunk(run.run_id, chunk.chunk_id, actor="agent")


def test_duplicate_active_run_is_blocked(service, restart_ticket):
    service.propose(restart_ticket, actor="alice")
    with pytest.raises(Exception):
        service.propose(restart_ticket, actor="alice")


def test_execution_claim_prevents_double_execution(service, restart_ticket):
    run = service.propose(restart_ticket, actor="alice")
    chunk = _agent_chunks(run)[0]
    service.decide_chunk(run.run_id, chunk.chunk_id, decision="approved", actor="bob")
    service.execute_chunk(run.run_id, chunk.chunk_id, actor="agent")

    # The approval was consumed by the first execution; a repeat is refused.
    with pytest.raises(DomainError):
        service.execute_chunk(run.run_id, chunk.chunk_id, actor="agent")
