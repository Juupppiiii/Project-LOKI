"""Tests for the read-only diagnostic gateway (Project STEPH workstream D).

The guarantees under test are mostly about what the gateway must REFUSE:
  * only catalogued operation ids run; unknown ids are denied (default-deny);
  * parameters are strictly validated (shape, bounds) before any backend call;
  * no operation can carry shell metacharacters into a target;
  * offline/mock results are clearly marked simulated and touch nothing;
  * output is bounded; every result is read-only.
"""

from __future__ import annotations

import pytest

from overlay import diagnostics

pytestmark = pytest.mark.unit


@pytest.fixture
def gw():
    # No live switches set in tests -> mock backend, touches nothing.
    return diagnostics.DiagnosticGateway()


# --- catalogue / default-deny ---------------------------------------------


def test_catalogue_lists_only_readonly_operations():
    cat = diagnostics.describe_catalogue()
    ids = {o["operation_id"] for o in cat}
    assert ids == {"service_status", "disk_free", "dns_lookup", "tcp_check", "event_log_query"}
    assert all(o["read_only"] for o in cat)


def test_unknown_operation_is_denied(gw):
    with pytest.raises(diagnostics.DiagnosticRefused):
        gw.run("restart_service", {"target": "host1", "service_name": "Spooler"})
    with pytest.raises(diagnostics.DiagnosticRefused):
        gw.run("delete_everything", {"target": "host1"})


# --- happy path (mock) -----------------------------------------------------


def test_service_status_returns_simulated_evidence(gw):
    r = gw.run("service_status", {"target": "host1", "service_name": "Spooler"})
    d = r.to_dict()
    assert d["read_only"] is True
    assert d["simulated"] is True
    assert d["evidence"]["service"] == "Spooler"
    assert d["operation_id"] == "service_status"


def test_tcp_check_and_disk_free_and_dns(gw):
    assert gw.run("tcp_check", {"target": "host1", "port": 443}).evidence["reachable"] in (True, False)
    assert "free_gb" in gw.run("disk_free", {"target": "host1"}).evidence
    assert gw.run("dns_lookup", {"name": "example.internal"}).evidence["name"] == "example.internal"


# --- validation / injection resistance ------------------------------------


def test_missing_required_parameter_is_refused(gw):
    with pytest.raises(diagnostics.DiagnosticRefused):
        gw.run("service_status", {"target": "host1"})  # no service_name


def test_shell_metacharacters_in_target_are_refused(gw):
    for bad in ["host1; rm -rf /", "host1 && calc", "host1|whoami", "$(reboot)", "a b"]:
        with pytest.raises(diagnostics.DiagnosticRefused):
            gw.run("service_status", {"target": bad, "service_name": "Spooler"})


def test_bad_service_name_is_refused(gw):
    with pytest.raises(diagnostics.DiagnosticRefused):
        gw.run("service_status", {"target": "host1", "service_name": "Spooler; Stop-Computer"})


def test_port_bounds_enforced(gw):
    with pytest.raises(diagnostics.DiagnosticRefused):
        gw.run("tcp_check", {"target": "host1", "port": 0})
    with pytest.raises(diagnostics.DiagnosticRefused):
        gw.run("tcp_check", {"target": "host1", "port": 70000})
    with pytest.raises(diagnostics.DiagnosticRefused):
        gw.run("tcp_check", {"target": "host1", "port": "not-a-number"})


def test_event_log_entries_are_bounded(gw):
    r = gw.run("event_log_query", {"target": "host1", "log_name": "System", "max_entries": 9999})
    # Mock caps at its sample size; the validator caps the request to <= 50.
    assert len(r.evidence["entries"]) <= 50


def test_ip_literal_target_is_accepted(gw):
    r = gw.run("service_status", {"target": "10.0.0.5", "service_name": "W32Time"})
    assert r.target == "10.0.0.5"


# --- allowlist behaviour (simulated live) ---------------------------------


def test_live_requires_target_allowlist(monkeypatch):
    # Force the gateway to believe it is live, with an injected mock-like backend
    # so nothing real is touched, to exercise the allowlist branch.
    monkeypatch.setenv("LOKI_EXECUTION_MODE", "live")
    monkeypatch.setenv("LOKI_DIAGNOSTICS_ENABLED", "1")
    monkeypatch.setenv("LOKI_DIAGNOSTICS_TARGETS", "allowed-host")

    class _B:
        name = "test-live-backend"
        def read(self, operation_id, params):
            return {"ok": True}

    gw = diagnostics.DiagnosticGateway(backend=_B())
    assert gw.live is True
    # A non-allowlisted target is refused before the backend is called.
    with pytest.raises(diagnostics.DiagnosticRefused):
        gw.run("service_status", {"target": "other-host", "service_name": "Spooler"})
    # The allowlisted target is permitted.
    r = gw.run("service_status", {"target": "allowed-host", "service_name": "Spooler"})
    assert r.simulated is False  # a real (injected) backend answered


def test_live_enforces_port_allowlist(monkeypatch):
    monkeypatch.setenv("LOKI_EXECUTION_MODE", "live")
    monkeypatch.setenv("LOKI_DIAGNOSTICS_ENABLED", "1")
    monkeypatch.setenv("LOKI_DIAGNOSTICS_TARGETS", "allowed-host")
    monkeypatch.setenv("LOKI_DIAGNOSTICS_PORTS", "443")

    class _B:
        name = "b"
        def read(self, operation_id, params):
            return {"reachable": True}

    gw = diagnostics.DiagnosticGateway(backend=_B())
    with pytest.raises(diagnostics.DiagnosticRefused):
        gw.run("tcp_check", {"target": "allowed-host", "port": 22})  # not allowlisted
    assert gw.run("tcp_check", {"target": "allowed-host", "port": 443}).evidence["reachable"] is True


# --- service integration: auth + audit ------------------------------------


def test_service_runs_and_audits(service):
    out = service.run_diagnostic(
        "service_status", {"target": "host1", "service_name": "Spooler"}, actor="inv@x", ticket_ref="INC1"
    )
    assert out["read_only"] is True
    events = [e.to_dict() for e in service.store.list_audit("INC1")]
    assert any(e["event"] == "diagnostic.ran" for e in events)


def test_service_refusal_is_audited(service):
    from overlay.domain import DomainError

    with pytest.raises(DomainError):
        service.run_diagnostic("reboot_everything", {}, actor="inv@x", ticket_ref="INC2")
    events = [e.to_dict() for e in service.store.list_audit("INC2")]
    assert any(e["event"] == "diagnostic.refused" for e in events)
