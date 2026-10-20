"""Contract tests for the connector gateway.

These prove the default-deny validation every connector (real or fake) must
pass before it can act. No real target system is contacted.
"""

from __future__ import annotations

import dataclasses

import pytest

from overlay import action_catalog, planner
from overlay.action_gateway import ActionGateway
from overlay.connectors import ConnectorRefused, build_envelope
from overlay.domain import Approval, new_id

pytestmark = pytest.mark.integration


def _run(service, restart_ticket):
    return service.propose(restart_ticket, actor="alice")


def _agent_chunk(run):
    # The read-only checks chunk contains an agent-eligible R0 step.
    for chunk in run.chunks:
        if chunk.owner.value == "ai":
            return chunk
    raise AssertionError("no agent-owned chunk in plan")


def _envelope_for(run, chunk):
    step = run.step(chunk.step_ids[0])
    definition = action_catalog.resolve(step.action)
    approval = Approval(
        approval_id=new_id("apr"),
        run_id=run.run_id,
        chunk_id=chunk.chunk_id,
        step_ids=list(chunk.step_ids),
        binding_digest=planner.binding_digest(run, chunk),
        decision="approved",
        actor="approver",
    )
    envelope = build_envelope(run, chunk, step, approval, definition, "alice")
    return envelope, step


def test_valid_agent_step_dispatches_and_is_simulated(service, restart_ticket):
    run = _run(service, restart_ticket)
    chunk = _agent_chunk(run)
    envelope, step = _envelope_for(run, chunk)

    result = ActionGateway().dispatch(envelope, step)
    assert result.simulated is True
    assert result.summary.startswith("[SIM]")
    assert result.connector == "simulation"


def test_missing_approval_binding_is_refused(service, restart_ticket):
    run = _run(service, restart_ticket)
    chunk = _agent_chunk(run)
    envelope, step = _envelope_for(run, chunk)
    tampered = dataclasses.replace(envelope, approval_id="", approval_digest="")
    with pytest.raises(ConnectorRefused):
        ActionGateway().dispatch(tampered, step)


def test_mismatched_catalog_version_is_refused(service, restart_ticket):
    run = _run(service, restart_ticket)
    chunk = _agent_chunk(run)
    envelope, step = _envelope_for(run, chunk)
    tampered = dataclasses.replace(envelope, catalog_version="sha256:tampered")
    with pytest.raises(ConnectorRefused):
        ActionGateway().dispatch(tampered, step)


def test_bad_idempotency_key_is_refused(service, restart_ticket):
    run = _run(service, restart_ticket)
    chunk = _agent_chunk(run)
    envelope, step = _envelope_for(run, chunk)
    tampered = dataclasses.replace(envelope, idempotency_key="not-a-valid-key")
    with pytest.raises(ConnectorRefused):
        ActionGateway().dispatch(tampered, step)


def test_unresolved_parameter_is_refused(service, restart_ticket):
    run = _run(service, restart_ticket)
    chunk = _agent_chunk(run)
    envelope, step = _envelope_for(run, chunk)
    tampered = dataclasses.replace(
        envelope, parameters={**envelope.parameters, "server": "<server?>"}
    )
    with pytest.raises(ConnectorRefused):
        ActionGateway().dispatch(tampered, step)


def test_unknown_execution_mode_cannot_be_constructed(monkeypatch):
    # Only 'simulation' and 'live' are valid; anything else fails at startup.
    monkeypatch.setenv("BUCKY_EXECUTION_MODE", "yolo")
    with pytest.raises(RuntimeError):
        ActionGateway()


def test_default_gateway_is_simulation_only(monkeypatch):
    # With no override the gateway is simulation mode and registers no real
    # connector. A simulation run executes against the simulation connector.
    monkeypatch.delenv("BUCKY_EXECUTION_MODE", raising=False)
    gateway = ActionGateway()
    assert gateway.mode == "simulation"


def test_simulation_run_cannot_reach_a_live_gateway(service, restart_ticket):
    # A simulation-mode envelope dispatched by a live-mode gateway is refused:
    # the run mode and gateway mode must agree.
    run = _run(service, restart_ticket)
    chunk = _agent_chunk(run)
    envelope, step = _envelope_for(run, chunk)  # envelope.mode == "simulation"
    live_gateway = ActionGateway(mode="live")
    with pytest.raises(ConnectorRefused):
        live_gateway.dispatch(envelope, step)


def test_live_gateway_still_refuses_human_only_action(service, restart_ticket):
    # Even in live mode, a human-only action (run_command/reboot) can never be
    # dispatched. Build an envelope for a human-only catalogue action.
    run = _run(service, restart_ticket)
    chunk = _agent_chunk(run)
    step = run.step(chunk.step_ids[0])
    human_def = action_catalog.resolve("run_command")
    approval = Approval(
        approval_id=new_id("apr"),
        run_id=run.run_id,
        chunk_id=chunk.chunk_id,
        step_ids=list(chunk.step_ids),
        binding_digest=planner.binding_digest(run, chunk),
        decision="approved",
        actor="approver",
    )
    envelope = build_envelope(run, chunk, step, approval, human_def, "alice")
    envelope = dataclasses.replace(envelope, mode="live")
    with pytest.raises(ConnectorRefused):
        ActionGateway(mode="live").dispatch(envelope, step)


def test_unregistered_connector_is_refused(service, restart_ticket):
    run = _run(service, restart_ticket)
    chunk = _agent_chunk(run)
    envelope, step = _envelope_for(run, chunk)
    # Point the action at a connector the gateway does not register. Because the
    # gateway checks the catalogue binding first, an unknown connector cannot be
    # smuggled past validation.
    tampered = dataclasses.replace(envelope, connector="winrm", operation="restart")
    with pytest.raises(ConnectorRefused):
        ActionGateway().dispatch(tampered, step)
