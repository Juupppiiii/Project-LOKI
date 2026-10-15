"""Tests for postcondition verification.

The guarantee: after a connector reports success, the step is only marked
succeeded if its postcondition is CONFIRMED. A simulated action verifies via a
pure consistency check. A real (non-simulated) result with no registered check
must FAIL CLOSED -- 'unverified', never a false 'verified' -- and that stops the
plan for a human rather than continuing on an unconfirmed state change.
"""

from __future__ import annotations

import pytest

from overlay import verification
from overlay.action_catalog import resolve
from overlay.connectors import ActionEnvelope, ActionResult


pytestmark = pytest.mark.unit


def _envelope(**over):
    base = dict(
        run_id="r",
        chunk_id="c",
        step_id="s",
        approval_id="a",
        approval_digest="d",
        action="restart_service",
        action_version="1.0.0",
        catalog_version="sha256:x",
        connector="simulation",
        operation="restart_service",
        target_ref="cmdb:host1",
        parameters={"service_name": "Netprobe"},
        risk="R2",
        actor="agent",
        triggered_by="human@x",
        mode="simulation",
        issued_at="2026-01-01T00:00:00+00:00",
        idempotency_key="exec:abc",
    )
    base.update(over)
    return ActionEnvelope(**base)


def test_simulated_result_verifies_when_consistent():
    defn = resolve("restart_service")
    env = _envelope()
    result = ActionResult(
        summary="[SIM] service restarted, status Running (host1)",
        connector="simulation",
        operation="restart_service",
        simulated=True,
        evidence={"target_ref": "cmdb:host1"},
    )
    outcome = verification.verify(defn, env, result)
    assert outcome.status is verification.VerificationStatus.VERIFIED
    assert outcome.ok is True
    assert outcome.blocks is False
    assert all(c["passed"] for c in outcome.checks)


def test_simulated_result_fails_when_operation_mismatches():
    defn = resolve("restart_service")
    env = _envelope()
    # A result claiming a different operation than was dispatched is inconsistent.
    result = ActionResult(
        summary="[SIM] something else",
        connector="simulation",
        operation="observe",  # != restart_service
        simulated=True,
    )
    outcome = verification.verify(defn, env, result)
    assert outcome.status is verification.VerificationStatus.UNVERIFIED
    assert outcome.blocks is True


def test_simulated_result_fails_on_empty_summary():
    defn = resolve("check")
    env = _envelope(action="check", operation="observe")
    result = ActionResult(summary="   ", connector="simulation", operation="observe", simulated=True)
    outcome = verification.verify(defn, env, result)
    assert outcome.status is verification.VerificationStatus.UNVERIFIED


def test_real_result_without_registered_check_fails_closed():
    # A non-simulated result for an action with no live verifier must NOT be
    # assumed successful. This is the core fail-closed guarantee.
    defn = resolve("servicenow.work_note.post")
    env = _envelope(
        action="servicenow.work_note.post",
        connector="servicenow",
        operation="post_work_note",
        parameters={"note": "hello"},
        mode="live",
    )
    result = ActionResult(
        summary="work note posted to INC1",
        connector="servicenow",
        operation="post_work_note",
        simulated=False,  # a REAL write
    )
    outcome = verification.verify(defn, env, result)
    assert outcome.status is verification.VerificationStatus.UNVERIFIED
    assert outcome.blocks is True
    assert outcome.ok is False


def test_outcome_serialises():
    defn = resolve("check")
    env = _envelope(action="check", operation="observe")
    result = ActionResult(summary="ok", connector="simulation", operation="observe", simulated=True)
    d = verification.verify(defn, env, result).to_dict()
    assert set(d) == {"status", "detail", "checks"}
    assert d["status"] == "verified"


def test_execute_records_verification_in_audit(service, restart_ticket, monkeypatch):
    # End-to-end: a simulated run executes, and the step.executed audit record
    # now carries a 'verification' block proving the postcondition was checked.
    monkeypatch.setenv("LOKI_ENFORCE_SOD", "0")
    run = service.propose(restart_ticket, actor="op@x")
    # approve + execute the first agent chunk
    first_chunk = run.chunks[0]
    service.decide_chunk(run.run_id, first_chunk.chunk_id, decision="approved", actor="approver@x")
    service.execute_chunk(run.run_id, first_chunk.chunk_id, actor="runner@x")

    events = service.audit(run.run_id)
    executed = [e for e in events if e["event"] == "step.executed"]
    assert executed, "expected at least one executed step"
    for e in executed:
        v = e["detail"].get("verification")
        assert v is not None, "step.executed must carry a verification record"
        assert v["status"] == "verified"
