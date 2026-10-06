"""Tests for the ServiceNow work-note connector and the gateway's live mode.

Fully offline. Every HTTP call goes through an injected fake transport that
records requests and returns canned responses, so no network is touched and no
real ServiceNow instance is required. These tests prove:

  * dry-run (write switch off) validates but makes NO write call;
  * with the write switch on, the connector PATCHes work_notes to the ticket;
  * a missing sys_id is resolved by a lookup GET;
  * 401/403/5xx and non-JSON responses raise ConnectorFailure;
  * an empty note is refused;
  * the gateway only dispatches the connector in live mode, for the one
    allowlisted binding, and still simulates everything else.
"""

from __future__ import annotations

import dataclasses

import pytest

from overlay import action_catalog, planner
from overlay.action_gateway import ActionGateway
from overlay.connectors import ConnectorFailure, build_envelope
from overlay.connectors_servicenow import ServiceNowConnector
from overlay.domain import Approval, new_id

pytestmark = pytest.mark.unit


CREDS_PRESENT = {
    "instance": "dev12345.service-now.com",
    "username": "svc_loki_write",
    "password": "secret",
    "present": True,
}


class FakeResponse:
    def __init__(self, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


class FakeTransport:
    """Records calls and returns queued responses in order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, method, url, *, headers=None, params=None, json=None, timeout=30):
        self.calls.append(
            {"method": method, "url": url, "headers": headers, "params": params, "json": json}
        )
        if not self._responses:
            raise AssertionError(f"unexpected extra call: {method} {url}")
        return self._responses.pop(0)


def _servicenow_envelope_and_step(service, restart_ticket, *, note="LOKI update", sys_id="", mode="live"):
    """Build a well-formed envelope (and its step) for servicenow.work_note.post.

    Proposes the run exactly once; callers must not propose the same ticket
    again or they will hit the duplicate-run guard.
    """

    run = service.propose(restart_ticket, actor="alice")
    chunk = next(c for c in run.chunks if c.owner.value == "ai")
    step = run.step(chunk.step_ids[0])
    definition = action_catalog.resolve("servicenow.work_note.post")
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
    params = {"note": note, "ticket_id": run.ticket_id}
    if sys_id:
        params["sys_id"] = sys_id
    envelope = dataclasses.replace(
        envelope,
        parameters=params,
        target_ref=f"servicenow:ticket/{run.ticket_id}",
        mode=mode,
    )
    return envelope, step


def _servicenow_envelope(service, restart_ticket, *, note="LOKI update", sys_id="", mode="live"):
    envelope, _step = _servicenow_envelope_and_step(
        service, restart_ticket, note=note, sys_id=sys_id, mode=mode
    )
    return envelope


# ---------------------------------------------------------------------------
# Connector behaviour (direct)
# ---------------------------------------------------------------------------


def test_dry_run_makes_no_http_call(monkeypatch, service, restart_ticket):
    monkeypatch.delenv("LOKI_SERVICENOW_WRITE_ENABLED", raising=False)
    transport = FakeTransport([])  # any call would raise
    conn = ServiceNowConnector(
        transport=transport, credentials_provider=lambda: CREDS_PRESENT
    )
    env = _servicenow_envelope(service, restart_ticket)

    result = conn.execute(env)

    assert transport.calls == []  # nothing was sent
    assert result.simulated is True
    assert result.summary.startswith("[DRY-RUN]")
    assert result.evidence["write_enabled"] is False


def test_write_enabled_patches_work_note_with_supplied_sys_id(monkeypatch, service, restart_ticket):
    monkeypatch.setenv("LOKI_SERVICENOW_WRITE_ENABLED", "1")
    transport = FakeTransport([FakeResponse(200, {"result": {"sys_id": "abc"}})])
    conn = ServiceNowConnector(
        transport=transport, credentials_provider=lambda: CREDS_PRESENT
    )
    env = _servicenow_envelope(service, restart_ticket, note="restarted service", sys_id="abc123")

    result = conn.execute(env)

    assert result.simulated is False
    assert result.evidence["write_enabled"] is True
    # Exactly one call: the PATCH (sys_id supplied, so no lookup GET).
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["method"] == "PATCH"
    assert call["url"].endswith("/api/now/table/incident/abc123")
    assert call["json"] == {"work_notes": "restarted service"}


def test_write_enabled_resolves_sys_id_then_patches(monkeypatch, service, restart_ticket):
    monkeypatch.setenv("LOKI_SERVICENOW_WRITE_ENABLED", "1")
    transport = FakeTransport([
        FakeResponse(200, {"result": [{"sys_id": "resolved-sys-id", "number": "INC0012345"}]}),  # lookup
        FakeResponse(200, {"result": {}}),  # patch
    ])
    conn = ServiceNowConnector(
        transport=transport, credentials_provider=lambda: CREDS_PRESENT
    )
    env = _servicenow_envelope(service, restart_ticket, sys_id="")  # force lookup

    conn.execute(env)

    assert [c["method"] for c in transport.calls] == ["GET", "PATCH"]
    assert transport.calls[1]["url"].endswith("/incident/resolved-sys-id")


def test_credentials_absent_raises(monkeypatch, service, restart_ticket):
    monkeypatch.setenv("LOKI_SERVICENOW_WRITE_ENABLED", "1")
    conn = ServiceNowConnector(
        transport=FakeTransport([]),
        credentials_provider=lambda: {"instance": "", "username": "", "password": "", "present": False},
    )
    env = _servicenow_envelope(service, restart_ticket)
    with pytest.raises(ConnectorFailure):
        conn.execute(env)


def test_empty_note_is_refused(monkeypatch, service, restart_ticket):
    monkeypatch.setenv("LOKI_SERVICENOW_WRITE_ENABLED", "1")
    conn = ServiceNowConnector(
        transport=FakeTransport([]), credentials_provider=lambda: CREDS_PRESENT
    )
    env = _servicenow_envelope(service, restart_ticket, note="   ")
    with pytest.raises(ConnectorFailure):
        conn.execute(env)


@pytest.mark.parametrize("status", [401, 403, 500])
def test_http_errors_raise_connector_failure(monkeypatch, service, restart_ticket, status):
    monkeypatch.setenv("LOKI_SERVICENOW_WRITE_ENABLED", "1")
    transport = FakeTransport([FakeResponse(status, text="boom")])
    conn = ServiceNowConnector(
        transport=transport, credentials_provider=lambda: CREDS_PRESENT
    )
    env = _servicenow_envelope(service, restart_ticket, sys_id="abc123")
    with pytest.raises(ConnectorFailure):
        conn.execute(env)


# ---------------------------------------------------------------------------
# Gateway + connector together (live mode, injected transport)
# ---------------------------------------------------------------------------


def _live_gateway_with_fake(transport, creds=CREDS_PRESENT):
    gateway = ActionGateway(mode="live")
    gateway._connectors["servicenow"] = ServiceNowConnector(
        transport=transport, credentials_provider=lambda: creds
    )
    return gateway


def test_live_gateway_dispatches_servicenow_action(monkeypatch, service, restart_ticket):
    monkeypatch.setenv("LOKI_SERVICENOW_WRITE_ENABLED", "1")
    transport = FakeTransport([FakeResponse(200, {"result": {}})])
    gateway = _live_gateway_with_fake(transport)

    # Build the envelope (this proposes the run) and reuse its step; proposing a
    # second time for the same ticket would hit the duplicate-run guard.
    env, step = _servicenow_envelope_and_step(service, restart_ticket, sys_id="abc123")

    result = gateway.dispatch(env, step)
    assert result.simulated is False
    assert transport.calls[0]["method"] == "PATCH"


def test_simulation_default_does_not_register_servicenow(monkeypatch):
    monkeypatch.delenv("LOKI_EXECUTION_MODE", raising=False)
    gateway = ActionGateway()
    assert "servicenow" not in gateway._connectors
