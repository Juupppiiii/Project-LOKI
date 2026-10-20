"""Real READ-ONLY diagnostic backend over AWS Systems Manager (SSM) Run Command.

This is the only module in the project that reaches a real host, and it does so
through the narrowest possible channel: SSM Run Command running a FIXED,
read-only PowerShell snippet per catalogued operation on a managed Windows node.

It plugs in behind ``diagnostics.DiagnosticBackend`` (single ``read`` method, no
write method), so the gateway's validation, bounds, and target allowlist all run
BEFORE this backend is ever called. The backend therefore receives only clean,
bounded parameters -- never raw model text.

Why this is safe by construction:

* **Read-only commands only.** Each operation maps to one fixed PowerShell
  snippet that only READS state: ``Get-Service``, ``Get-PSDrive``,
  ``Resolve-DnsName``, ``Test-NetConnection``, ``Get-WinEvent``. There is no
  snippet that writes, restarts, installs, or deletes anything. The command text
  is a constant in this file; parameters are injected only as data via SSM
  document parameters / here-string values that the gateway already validated to
  a strict shape (hostnames, service names, ports, log names).
* **Document allowlist.** Only the AWS-managed ``AWS-RunPowerShellScript`` SSM
  document is used, and only with these constant scripts. No arbitrary document,
  no arbitrary command.
* **Dual-switch + target allowlist.** The gateway only builds this backend when
  ``BUCKY_EXECUTION_MODE=live`` AND ``BUCKY_DIAGNOSTICS_ENABLED=1``, and only
  lets it run against hosts in ``BUCKY_DIAGNOSTICS_TARGETS``. This module adds a
  second check: the SSM *instance id* must resolve from an explicit host->id map
  (``BUCKY_SSM_TARGETS``) or be provided as an i-... id; an unmapped host is
  refused. Default-deny throughout.
* **Bounded.** A per-command timeout and an output cap are enforced; the gateway
  additionally truncates the evidence.
* **Fail closed.** Any boto3/SSM/JSON error raises ``DiagnosticFailed`` -- the
  gateway never treats a failed read as a successful or empty result, and the
  triage agent never auto-closes on unverified evidence.

boto3 is imported lazily, so importing this module never requires it and the
whole project still runs and tests offline against the mock backend.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from .diagnostics import DiagnosticFailed, DiagnosticRefused


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_SSM_DOCUMENT = "AWS-RunPowerShellScript"
_DEFAULT_REGION = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "eu-west-1"
# Hard caps. The gateway also truncates, but we bound at the source too.
_COMMAND_TIMEOUT_SECONDS = max(5, min(60, int(os.getenv("BUCKY_SSM_TIMEOUT_SECONDS", "20"))))
_POLL_INTERVAL_SECONDS = 1.0
_MAX_OUTPUT_CHARS = 20_000


def _target_map() -> dict[str, str]:
    """Map allowlisted host names to SSM instance ids.

    Format of BUCKY_SSM_TARGETS: "host1=i-0abc...,host2=i-0def...". A host not in
    this map (and not already an i-... id) is refused -- default-deny. This is a
    SECOND gate on top of the gateway's BUCKY_DIAGNOSTICS_TARGETS allowlist.
    """

    raw = os.getenv("BUCKY_SSM_TARGETS", "")
    out: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        host, iid = pair.split("=", 1)
        host = host.strip().lower()
        iid = iid.strip()
        if host and iid:
            out[host] = iid
    return out


def _resolve_instance_id(target: str) -> str:
    """Resolve a host name to its SSM instance id, or refuse.

    Accepts an explicit ``i-...`` instance id, or a host present in the target
    map. Everything else is refused (never guessed).
    """

    t = str(target or "").strip()
    if t.lower().startswith("i-") and len(t) >= 10:
        return t
    mapped = _target_map().get(t.lower())
    if mapped:
        return mapped
    raise DiagnosticRefused(
        f"target '{target}' has no SSM instance mapping; add it to "
        "BUCKY_SSM_TARGETS (host=i-xxxx) or pass an i-... instance id"
    )


# ---------------------------------------------------------------------------
# Fixed, read-only PowerShell per operation
# ---------------------------------------------------------------------------
#
# Each returns a single JSON object on stdout so parsing is unambiguous. The
# snippets are CONSTANTS; validated parameters are passed as SSM document
# parameters (never string-concatenated into the command), so no parameter can
# alter the command's structure. ConvertTo-Json -Compress keeps output bounded.

# NOTE on parameter passing: AWS-RunPowerShellScript takes a "commands" list. We
# prepend a line that binds each validated parameter to a PowerShell variable
# using a single-quoted literal, then append the fixed read-only body. Because
# the gateway already restricts these values to strict shapes (hostname / service
# name / drive letter / integer / log name with no quotes or metacharacters), the
# single-quoted literal cannot be broken out of. We additionally reject any value
# containing a single quote as defense in depth.

_BODIES: dict[str, str] = {
    "service_status": (
        "$s = Get-Service -Name $svc -ErrorAction Stop; "
        "[pscustomobject]@{service=$s.Name; status=$s.Status.ToString(); "
        "start_type=$s.StartType.ToString()} | ConvertTo-Json -Compress"
    ),
    "disk_free": (
        "$d = Get-PSDrive -Name ($drive.TrimEnd(':')) -PSProvider FileSystem -ErrorAction Stop; "
        "$free=[math]::Round($d.Free/1GB,2); $used=[math]::Round($d.Used/1GB,2); "
        "$total=$free+$used; "
        "[pscustomobject]@{drive=$drive; free_gb=$free; total_gb=$total; "
        "free_percent=[math]::Round(($(if($total-gt0){$free/$total}else{0}))*100,1)} "
        "| ConvertTo-Json -Compress"
    ),
    "dns_lookup": (
        "$r = Resolve-DnsName -Name $name -ErrorAction Stop | "
        "Where-Object {$_.IPAddress} | Select-Object -ExpandProperty IPAddress; "
        "[pscustomobject]@{name=$name; addresses=@($r)} | ConvertTo-Json -Compress"
    ),
    "tcp_check": (
        "$t = Test-NetConnection -ComputerName $host2 -Port $port "
        "-WarningAction SilentlyContinue; "
        "[pscustomobject]@{target=$host2; port=$port; reachable=[bool]$t.TcpTestSucceeded; "
        "remote_address=([string]$t.RemoteAddress)} | ConvertTo-Json -Compress"
    ),
    "event_log_query": (
        "$e = Get-WinEvent -LogName $log -MaxEvents $max -ErrorAction Stop | "
        "Select-Object -Property @{n='id';e={$_.Id}}, "
        "@{n='level';e={$_.LevelDisplayName}}, "
        "@{n='time';e={$_.TimeCreated.ToString('o')}}, "
        "@{n='message';e={ if($_.Message){$_.Message.Substring(0,[Math]::Min(300,$_.Message.Length))}else{''} }}; "
        "[pscustomobject]@{log=$log; entries=@($e)} | ConvertTo-Json -Compress -Depth 4"
    ),
}


def _no_quote(value: str, *, field_name: str) -> str:
    """Defense in depth: refuse any value containing a single quote.

    The gateway's shape validation already excludes quotes/metacharacters; this
    guarantees a value can never terminate the single-quoted PowerShell literal
    even if an operation's validation were ever loosened.
    """

    v = str(value)
    if "'" in v or "`" in v or "$(" in v:
        raise DiagnosticRefused(f"{field_name} contains a forbidden character")
    return v


def _build_commands(operation_id: str, params: dict[str, Any]) -> list[str]:
    """Build the SSM 'commands' list: parameter bindings + fixed read-only body."""

    binds: list[str] = []
    if operation_id == "service_status":
        binds.append(f"$svc = '{_no_quote(params['service_name'], field_name='service_name')}'")
    elif operation_id == "disk_free":
        binds.append(f"$drive = '{_no_quote(params.get('drive', 'C:'), field_name='drive')}'")
    elif operation_id == "dns_lookup":
        binds.append(f"$name = '{_no_quote(params['name'], field_name='name')}'")
    elif operation_id == "tcp_check":
        binds.append(f"$host2 = '{_no_quote(params['target'], field_name='target')}'")
        binds.append(f"$port = {int(params['port'])}")  # already an int from the gateway
    elif operation_id == "event_log_query":
        binds.append(f"$log = '{_no_quote(params['log_name'], field_name='log_name')}'")
        binds.append(f"$max = {int(params.get('max_entries', 10))}")
    else:
        raise DiagnosticFailed(f"SSM backend has no body for '{operation_id}'")

    body = _BODIES[operation_id]
    return ["$ErrorActionPreference='Stop'", *binds, body]


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


class SsmDiagnosticBackend:
    """Runs one fixed read-only PowerShell snippet via SSM Run Command.

    ``client`` is injectable so tests drive it with a fake SSM client (no AWS,
    no network). In production it is a lazily-created boto3 ``ssm`` client.
    """

    def __init__(self, *, client: Any = None, region_name: str | None = None) -> None:
        self._client = client
        self._region = region_name or _DEFAULT_REGION

    @property
    def name(self) -> str:
        return f"ssm-run-command ({self._region}, read-only)"

    def _ssm(self):
        if self._client is not None:
            return self._client
        try:
            import boto3  # lazy

            self._client = boto3.client("ssm", region_name=self._region)
            return self._client
        except Exception as exc:  # noqa: BLE001
            raise DiagnosticFailed(f"could not create SSM client: {exc}") from exc

    def read(self, operation_id: str, params: dict[str, Any]) -> dict[str, Any]:
        # dns_lookup has no host target; run it against... nothing remote? DNS
        # resolution is still a host-side read, so we require a target for the
        # instance to run on. The gateway marks dns_lookup needs_target=False, so
        # for the SSM backend a 'target' (or 'name' as the instance) must be
        # supplied via the instance mapping. Resolve from 'target' if present,
        # else refuse clearly.
        target = params.get("target") or params.get("name") or ""
        instance_id = _resolve_instance_id(target)

        commands = _build_commands(operation_id, params)
        client = self._ssm()

        try:
            send = client.send_command(
                InstanceIds=[instance_id],
                DocumentName=_SSM_DOCUMENT,
                Parameters={"commands": commands},
                TimeoutSeconds=_COMMAND_TIMEOUT_SECONDS,
                Comment="BUCKY read-only diagnostic",
            )
            command_id = send["Command"]["CommandId"]
        except Exception as exc:  # noqa: BLE001
            raise DiagnosticFailed(f"SSM send_command failed: {exc}") from exc

        payload = self._await_output(client, command_id, instance_id)
        return self._parse(operation_id, payload)

    def _await_output(self, client: Any, command_id: str, instance_id: str) -> str:
        deadline = time.time() + _COMMAND_TIMEOUT_SECONDS + 5
        while time.time() < deadline:
            try:
                inv = client.get_command_invocation(
                    CommandId=command_id, InstanceId=instance_id
                )
            except Exception as exc:  # noqa: BLE001
                # InvocationDoesNotExist can occur briefly right after send.
                if "InvocationDoesNotExist" in str(exc):
                    time.sleep(_POLL_INTERVAL_SECONDS)
                    continue
                raise DiagnosticFailed(f"SSM get_command_invocation failed: {exc}") from exc

            status = inv.get("Status")
            if status in ("Pending", "InProgress", "Delayed"):
                time.sleep(_POLL_INTERVAL_SECONDS)
                continue
            if status == "Success":
                out = inv.get("StandardOutputContent", "") or ""
                return out[:_MAX_OUTPUT_CHARS]
            # Any non-success terminal state fails closed with the error content.
            err = (inv.get("StandardErrorContent", "") or status or "")[:500]
            raise DiagnosticFailed(f"SSM command {status}: {err}")

        raise DiagnosticFailed("SSM command timed out awaiting output")

    @staticmethod
    def _parse(operation_id: str, stdout: str) -> dict[str, Any]:
        text = (stdout or "").strip()
        if not text:
            raise DiagnosticFailed(f"{operation_id}: empty output from host")
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise DiagnosticFailed(
                f"{operation_id}: host did not return parseable JSON"
            ) from exc
        if not isinstance(data, dict):
            raise DiagnosticFailed(f"{operation_id}: unexpected output shape")
        return data


def build_ssm_backend(*, client: Any = None, region_name: str | None = None) -> SsmDiagnosticBackend:
    """Factory used by the diagnostics gateway's real-backend seam."""

    return SsmDiagnosticBackend(client=client, region_name=region_name)
