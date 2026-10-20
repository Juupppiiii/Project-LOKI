"""Tests for the real READ-ONLY SSM diagnostic backend.

Everything runs offline against a fake SSM client -- no AWS, no network. The
guarantees under test are the safety-critical ones:

  * each operation issues ONLY a fixed read-only PowerShell body (Get-Service,
    Get-PSDrive, Resolve-DnsName, Test-NetConnection, Get-WinEvent) via the
    AWS-RunPowerShellScript document -- never an arbitrary command;
  * parameters are bound as single-quoted literals and a value containing a
    quote / backtick / $( is refused (defense in depth);
  * a host with no SSM instance mapping is refused (default-deny);
  * an i-... instance id is accepted directly;
  * a failed, timed-out, empty, or non-JSON result FAILS CLOSED (DiagnosticFailed),
    never a false success;
  * the gateway only builds the real backend when the third switch is set.
"""

from __future__ import annotations

import json

import pytest

from overlay import diagnostics
from overlay import diagnostics_ssm as ssm

pytestmark = pytest.mark.unit


class FakeSsm:
    """Minimal SSM client: records send_command, returns a scripted invocation."""

    def __init__(self, *, stdout="", status="Success", stderr=""):
        self._stdout = stdout
        self._status = status
        self._stderr = stderr
        self.sent = []

    def send_command(self, **kwargs):
        self.sent.append(kwargs)
        return {"Command": {"CommandId": "cmd-123"}}

    def get_command_invocation(self, **kwargs):
        return {
            "Status": self._status,
            "StandardOutputContent": self._stdout,
            "StandardErrorContent": self._stderr,
        }


def _backend(fake):
    return ssm.SsmDiagnosticBackend(client=fake)


# --- instance resolution / default-deny -----------------------------------


def test_unmapped_host_is_refused(monkeypatch):
    monkeypatch.delenv("BUCKY_SSM_TARGETS", raising=False)
    with pytest.raises(diagnostics.DiagnosticRefused):
        ssm._resolve_instance_id("ldn1ws0059.corp.ad.tullib.com")


def test_explicit_instance_id_is_accepted():
    assert ssm._resolve_instance_id("i-0abc123def456").startswith("i-")


def test_mapped_host_resolves(monkeypatch):
    monkeypatch.setenv("BUCKY_SSM_TARGETS", "host-a=i-0aaa1111,host-b=i-0bbb2222")
    assert ssm._resolve_instance_id("HOST-A") == "i-0aaa1111"


# --- read-only command shape ----------------------------------------------


def test_only_runpowershell_document_and_readonly_body(monkeypatch):
    monkeypatch.setenv("BUCKY_SSM_TARGETS", "h1=i-0aaa1111")
    fake = FakeSsm(stdout=json.dumps({"service": "Spooler", "status": "Running", "start_type": "Automatic"}))
    result = _backend(fake).read("service_status", {"target": "h1", "service_name": "Spooler"})
    assert result["status"] == "Running"
    sent = fake.sent[0]
    assert sent["DocumentName"] == "AWS-RunPowerShellScript"
    body = " ".join(sent["Parameters"]["commands"])
    assert "Get-Service" in body
    # No write verbs anywhere in the issued command.
    for forbidden in ("Set-", "Stop-Service", "Restart-", "Remove-", "New-Item", "Start-Service"):
        assert forbidden not in body


def test_quote_injection_is_refused(monkeypatch):
    monkeypatch.setenv("BUCKY_SSM_TARGETS", "h1=i-0aaa1111")
    fake = FakeSsm(stdout="{}")
    with pytest.raises(diagnostics.DiagnosticRefused):
        _backend(fake).read("service_status", {"target": "h1", "service_name": "Spooler'; Stop-Service x #"})


def test_each_operation_uses_its_readonly_cmdlet(monkeypatch):
    monkeypatch.setenv("BUCKY_SSM_TARGETS", "h1=i-0aaa1111")
    cases = {
        "disk_free": ("Get-PSDrive", {"target": "h1"}, {"drive": "C:", "free_gb": 10, "total_gb": 100, "free_percent": 10}),
        "dns_lookup": ("Resolve-DnsName", {"target": "h1", "name": "h1"}, {"name": "h1", "addresses": ["10.0.0.1"]}),
        "tcp_check": ("Test-NetConnection", {"target": "h1", "port": 3389}, {"target": "h1", "port": 3389, "reachable": True}),
        "event_log_query": ("Get-WinEvent", {"target": "h1", "log_name": "System", "max_entries": 5}, {"log": "System", "entries": []}),
    }
    for op, (cmdlet, params, out) in cases.items():
        fake = FakeSsm(stdout=json.dumps(out))
        result = _backend(fake).read(op, params)
        body = " ".join(fake.sent[0]["Parameters"]["commands"])
        assert cmdlet in body, op
        assert isinstance(result, dict)


# --- fail closed -----------------------------------------------------------


def test_command_failure_fails_closed(monkeypatch):
    monkeypatch.setenv("BUCKY_SSM_TARGETS", "h1=i-0aaa1111")
    fake = FakeSsm(status="Failed", stderr="access denied")
    with pytest.raises(diagnostics.DiagnosticFailed):
        _backend(fake).read("service_status", {"target": "h1", "service_name": "Spooler"})


def test_empty_output_fails_closed(monkeypatch):
    monkeypatch.setenv("BUCKY_SSM_TARGETS", "h1=i-0aaa1111")
    fake = FakeSsm(stdout="   ")
    with pytest.raises(diagnostics.DiagnosticFailed):
        _backend(fake).read("disk_free", {"target": "h1"})


def test_non_json_output_fails_closed(monkeypatch):
    monkeypatch.setenv("BUCKY_SSM_TARGETS", "h1=i-0aaa1111")
    fake = FakeSsm(stdout="not json at all")
    with pytest.raises(diagnostics.DiagnosticFailed):
        _backend(fake).read("disk_free", {"target": "h1"})


# --- gateway wiring --------------------------------------------------------


def test_real_backend_refused_without_third_switch(monkeypatch):
    # live + diagnostics on, but the SSM switch off -> real path fails closed.
    monkeypatch.setenv("BUCKY_EXECUTION_MODE", "live")
    monkeypatch.setenv("BUCKY_DIAGNOSTICS_ENABLED", "1")
    monkeypatch.delenv("BUCKY_SSM_DIAGNOSTICS_ENABLED", raising=False)
    with pytest.raises(diagnostics.DiagnosticFailed):
        diagnostics.DiagnosticGateway()


def test_offline_default_is_mock_backend(monkeypatch):
    monkeypatch.delenv("BUCKY_EXECUTION_MODE", raising=False)
    monkeypatch.delenv("BUCKY_DIAGNOSTICS_ENABLED", raising=False)
    gw = diagnostics.DiagnosticGateway()
    assert gw.live is False
    result = gw.run("service_status", {"target": "anything", "service_name": "Spooler"})
    assert result.simulated is True
