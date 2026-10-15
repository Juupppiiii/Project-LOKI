"""Tests for the compensation / rollback seam.

Pairs with verification. When a real action's postcondition cannot be confirmed,
a step that declares a rollback can have it attempted before handing to a human;
a step with no rollback simply records that fact. Real (non-simulated) effects
with no registered live compensator FAIL CLOSED -- never a false "compensated".
"""

from __future__ import annotations

import pytest

from overlay import rollback
from overlay.connectors import ActionResult
from overlay.domain import ExecutionPolicy, Owner, RiskClass, StepRun

pytestmark = pytest.mark.unit


def _step(rollback_text: str = "") -> StepRun:
    return StepRun(
        step_id="stp1",
        sequence=1,
        action="restart_service",
        description="restart it",
        rendered="restart it",
        risk=RiskClass.R2,
        owner=Owner.AI,
        policy=ExecutionPolicy.APPROVE_STEP,
        rollback=rollback_text,
    )


def _result(simulated: bool) -> ActionResult:
    return ActionResult(
        summary="done",
        connector="simulation" if simulated else "servicenow",
        operation="restart_service",
        simulated=simulated,
    )


def test_no_rollback_declared():
    out = rollback.compensate(_step(""), None, _result(True))
    assert out.status is rollback.RollbackStatus.NO_ROLLBACK
    assert out.compensated is False
    assert out.needs_attention is False


def test_simulated_rollback_is_recorded_not_executed():
    out = rollback.compensate(_step("Stop-Service X; Start-Service X"), None, _result(True))
    assert out.status is rollback.RollbackStatus.COMPENSATED
    assert out.compensated is True
    assert "[SIM]" in out.detail


def test_real_effect_without_compensator_fails_closed():
    out = rollback.compensate(_step("undo it"), None, _result(False))
    assert out.status is rollback.RollbackStatus.FAILED
    assert out.needs_attention is True
    assert out.compensated is False


def test_has_rollback_helper():
    assert rollback.has_rollback(_step("undo")) is True
    assert rollback.has_rollback(_step("   ")) is False
    assert rollback.has_rollback(_step("")) is False


def test_outcome_serialises():
    d = rollback.compensate(_step("undo"), None, _result(True)).to_dict()
    assert set(d) == {"status", "detail", "checks"}
    assert d["status"] == "compensated"


def test_rollback_text_threads_from_procedure_into_steprun(service, restart_ticket):
    # A planned run's steps carry the rollback field (empty for the built-in
    # procedures that declare none, but the attribute is present and threaded).
    run = service.propose(restart_ticket, actor="alice")
    for s in run.steps:
        assert hasattr(s, "rollback")
        assert isinstance(s.rollback, str)
