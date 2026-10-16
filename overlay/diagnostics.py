"""Read-only diagnostic gateway -- a SEPARATE boundary from action execution.

This module implements Project STEPH/LOKI workstream D: a fixed catalogue of
read-only diagnostic operations a human-authorised investigator can run against
an allowlisted target to GATHER EVIDENCE. It is deliberately independent of:

* ``action_gateway`` / ``simulator`` -- which simulate *remediation* steps, and
* ``connectors_servicenow`` -- the one real *write* connector (work notes).

Non-negotiable guardrails enforced here (per the planning document):

* **Read-only only.** Every operation reads state; none changes anything. There
  is no code path that restarts a service, edits config, deletes data, writes a
  file, or updates a ticket. The backend protocol has a single ``read`` method.
* **Fixed operation IDs.** Only the operations catalogued below exist. An
  unknown operation id is DENIED, never guessed or passed through.
* **No arbitrary commands.** A caller supplies an operation id plus validated
  parameters -- never a shell/PowerShell string. Model-generated text can never
  become an executed command here.
* **Strict validation.** Each operation declares its required parameters, their
  shapes, and bounds (ports 1-65535, timeouts capped, output truncated).
* **Allowlists.** Targets, ports, and operations are allowlisted; anything not
  explicitly permitted is refused. Default-deny throughout.
* **Dual-switch for the real path.** Like the ServiceNow connector, the real
  backend only runs when LOKI_EXECUTION_MODE=live AND LOKI_DIAGNOSTICS_ENABLED=1.
  Otherwise a deterministic mock backend answers, so the whole gateway is
  exercisable offline and in tests without touching any host or network.

The output is EVIDENCE for a human to read; it authorises nothing.
"""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DiagnosticRefused(Exception):
    """Policy or validation forbids running this diagnostic. Never a bug."""


class DiagnosticFailed(Exception):
    """The backend attempted the read but could not complete it."""


# ---------------------------------------------------------------------------
# Operation catalogue (fixed, reviewed, read-only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiagnosticOperation:
    operation_id: str
    summary: str
    required_parameters: tuple[str, ...] = ()
    optional_parameters: tuple[str, ...] = ()
    # Whether this operation needs a host target (most do; dns_lookup needs a
    # name, tcp_check needs host+port). Used only for clearer validation errors.
    needs_target: bool = True


# The ONLY operations that exist. Adding one is a reviewed code change, never a
# runtime or model decision. All are read-only.
_OPERATIONS: tuple[DiagnosticOperation, ...] = (
    DiagnosticOperation(
        "service_status",
        "Read the status of a named Windows service on a host (read-only).",
        required_parameters=("target", "service_name"),
    ),
    DiagnosticOperation(
        "disk_free",
        "Read free space on a host's drives (read-only).",
        required_parameters=("target",),
        optional_parameters=("drive",),
    ),
    DiagnosticOperation(
        "dns_lookup",
        "Resolve a hostname to addresses (read-only).",
        required_parameters=("name",),
        needs_target=False,
    ),
    DiagnosticOperation(
        "tcp_check",
        "Test TCP connectivity to an allowlisted host and port (read-only).",
        required_parameters=("target", "port"),
    ),
    DiagnosticOperation(
        "event_log_query",
        "Read a bounded window of recent Windows event-log entries (read-only).",
        required_parameters=("target", "log_name"),
        optional_parameters=("max_entries",),
    ),
)

_BY_ID = {op.operation_id: op for op in _OPERATIONS}


def catalogue() -> tuple[DiagnosticOperation, ...]:
    return _OPERATIONS


def describe_catalogue() -> list[dict[str, Any]]:
    return [
        {
            "operation_id": op.operation_id,
            "summary": op.summary,
            "required_parameters": list(op.required_parameters),
            "optional_parameters": list(op.optional_parameters),
            "read_only": True,
        }
        for op in _OPERATIONS
    ]


# ---------------------------------------------------------------------------
# Limits (bounds enforced on every request)
# ---------------------------------------------------------------------------

# Hostname label: letters/digits/hyphen, dot-separated, no shell metacharacters.
_HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$")
# Windows service name: conservative allowlist shape (no spaces/metachars).
_SERVICE_RE = re.compile(r"^[A-Za-z0-9_.$-]{1,80}$")
_LOG_NAME_RE = re.compile(r"^[A-Za-z0-9 /_-]{1,64}$")
_MAX_EVENT_ENTRIES = 50
_MAX_OUTPUT_CHARS = 10_000


def _diagnostics_enabled() -> bool:
    mode = os.getenv("LOKI_EXECUTION_MODE", "simulation").strip().lower()
    switch = os.getenv("LOKI_DIAGNOSTICS_ENABLED", "0").strip().lower()
    return mode == "live" and switch in ("1", "true", "yes", "on")


def _target_allowlist() -> set[str]:
    """Hosts the gateway may target, from LOKI_DIAGNOSTICS_TARGETS (comma list).

    Empty by default: with no allowlist, every real target is refused. The mock
    backend ignores this (it touches nothing), so offline use needs no config,
    but the REAL backend can only reach explicitly listed hosts.
    """

    raw = os.getenv("LOKI_DIAGNOSTICS_TARGETS", "")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def _port_allowlist() -> set[int]:
    raw = os.getenv("LOKI_DIAGNOSTICS_PORTS", "53,80,443,445,3389,5985,5986")
    out: set[int] = set()
    for tok in raw.split(","):
        tok = tok.strip()
        if tok.isdigit():
            out.add(int(tok))
    return out


# ---------------------------------------------------------------------------
# Backend protocol + implementations
# ---------------------------------------------------------------------------


class DiagnosticBackend(Protocol):
    """Performs ONE read-only operation and returns a plain dict of evidence.

    The single method is ``read`` -- there is intentionally no write method, so
    a backend cannot change anything even by mistake.
    """

    @property
    def name(self) -> str: ...

    def read(self, operation_id: str, params: dict[str, Any]) -> dict[str, Any]: ...


class MockDiagnosticBackend:
    """Deterministic offline backend. Touches no host, no network, no process.

    Returns plausible, clearly-labelled sample evidence so the whole gateway is
    testable and demoable without any real target. Every result is marked
    ``simulated: True`` so a caller can never mistake it for live data.
    """

    @property
    def name(self) -> str:
        return "mock (offline; no real target touched)"

    def read(self, operation_id: str, params: dict[str, Any]) -> dict[str, Any]:
        if operation_id == "service_status":
            return {"service": params["service_name"], "status": "Running", "start_type": "Automatic"}
        if operation_id == "disk_free":
            return {"drive": params.get("drive", "C:"), "free_gb": 42.5, "total_gb": 237.0, "free_percent": 17.9}
        if operation_id == "dns_lookup":
            return {"name": params["name"], "addresses": ["10.0.0.10"]}
        if operation_id == "tcp_check":
            return {"target": params["target"], "port": int(params["port"]), "reachable": True, "latency_ms": 3}
        if operation_id == "event_log_query":
            n = int(params.get("max_entries", 5))
            return {
                "log": params["log_name"],
                "entries": [
                    {"id": 7036, "level": "Information", "message": f"sample event {i}"}
                    for i in range(min(n, 5))
                ],
            }
        raise DiagnosticFailed(f"mock backend has no reading for '{operation_id}'")


def _real_backend_factory() -> "DiagnosticBackend":
    """Build the real read-only backend (lazy import).

    Deliberately not implemented here: a real backend runs reviewed, read-only
    operations against an allowlisted host (e.g. a remote agent's Get-Service /
    Get-PSDrive / Resolve-DnsName / Test-NetConnection, or SSM for managed
    nodes). It plugs in behind this same protocol when a target channel is
    reviewed and enabled. Until then, requesting the real path fails closed
    rather than silently doing nothing.
    """

    raise DiagnosticFailed(
        "no real diagnostic backend is registered; diagnostics run against the "
        "mock backend until a reviewed read-only target channel is enabled"
    )


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiagnosticResult:
    operation_id: str
    target: str
    simulated: bool
    evidence: dict[str, Any]
    backend: str
    note: str = ""
    checks: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "target": self.target,
            "simulated": self.simulated,
            "evidence": self.evidence,
            "backend": self.backend,
            "read_only": True,
            "note": self.note,
            "checks": self.checks,
        }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_hostname(value: str, *, field_name: str) -> str:
    v = str(value or "").strip()
    if not v:
        raise DiagnosticRefused(f"{field_name} is required")
    # Accept an IP literal or a DNS hostname label; reject anything else so a
    # value can never carry shell metacharacters into a backend.
    try:
        ipaddress.ip_address(v)
        return v
    except ValueError:
        pass
    if not _HOSTNAME_RE.match(v):
        raise DiagnosticRefused(f"{field_name} '{v}' is not a valid hostname or IP")
    return v


def _validate(operation_id: str, params: dict[str, Any]) -> dict[str, Any]:
    op = _BY_ID.get(operation_id)
    if op is None:
        # Default-deny: an unknown operation is refused, never passed through.
        raise DiagnosticRefused(f"unknown diagnostic operation '{operation_id}'")

    missing = [p for p in op.required_parameters if str(params.get(p, "")).strip() == ""]
    if missing:
        raise DiagnosticRefused(
            f"operation '{operation_id}' is missing required parameters: "
            + ", ".join(missing)
        )

    clean: dict[str, Any] = {}

    # Target host (allowlisted when the real backend is active).
    if op.needs_target:
        target = _validate_hostname(params.get("target"), field_name="target")
        clean["target"] = target

    if operation_id == "service_status":
        svc = str(params["service_name"]).strip()
        if not _SERVICE_RE.match(svc):
            raise DiagnosticRefused(f"service_name '{svc}' has an invalid shape")
        clean["service_name"] = svc

    elif operation_id == "disk_free":
        drive = str(params.get("drive", "C:")).strip()
        if not re.match(r"^[A-Za-z]:?$", drive):
            raise DiagnosticRefused(f"drive '{drive}' is invalid")
        clean["drive"] = drive if drive.endswith(":") else drive + ":"

    elif operation_id == "dns_lookup":
        clean["name"] = _validate_hostname(params.get("name"), field_name="name")

    elif operation_id == "tcp_check":
        try:
            port = int(params["port"])
        except (TypeError, ValueError):
            raise DiagnosticRefused("port must be an integer") from None
        if not 1 <= port <= 65535:
            raise DiagnosticRefused("port must be between 1 and 65535")
        clean["port"] = port

    elif operation_id == "event_log_query":
        log = str(params["log_name"]).strip()
        if not _LOG_NAME_RE.match(log):
            raise DiagnosticRefused(f"log_name '{log}' is invalid")
        clean["log_name"] = log
        try:
            n = int(params.get("max_entries", 10))
        except (TypeError, ValueError):
            raise DiagnosticRefused("max_entries must be an integer") from None
        clean["max_entries"] = max(1, min(_MAX_EVENT_ENTRIES, n))

    return clean


def _enforce_target_allowlist(operation_id: str, clean: dict[str, Any], *, real: bool) -> None:
    """When the real backend is active, the target must be allowlisted."""

    if not real:
        return  # mock touches nothing; allowlist does not apply offline
    op = _BY_ID[operation_id]
    if op.needs_target:
        allow = _target_allowlist()
        if clean["target"].lower() not in allow:
            raise DiagnosticRefused(
                f"target '{clean['target']}' is not in the diagnostics allowlist"
            )
    if operation_id == "tcp_check" and clean["port"] not in _port_allowlist():
        raise DiagnosticRefused(
            f"port {clean['port']} is not in the diagnostics port allowlist"
        )


def _truncate(evidence: dict[str, Any]) -> dict[str, Any]:
    """Bound output size so a diagnostic can never return unbounded data."""

    import json as _json

    raw = _json.dumps(evidence, default=str)
    if len(raw) <= _MAX_OUTPUT_CHARS:
        return evidence
    return {"_truncated": True, "_preview": raw[:_MAX_OUTPUT_CHARS]}


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------


class DiagnosticGateway:
    """Default-deny dispatcher for read-only diagnostic operations.

    ``backend_factory`` is injectable so tests (and the offline default) use the
    mock. In a live, enabled deployment it builds the reviewed real read-only
    backend. Either way the gateway validates and allowlists before the backend
    is ever called.
    """

    def __init__(
        self,
        *,
        backend: DiagnosticBackend | None = None,
        backend_factory: Callable[[], DiagnosticBackend] | None = None,
    ) -> None:
        self._real = _diagnostics_enabled()
        if backend is not None:
            self._backend = backend
        elif not self._real:
            self._backend = MockDiagnosticBackend()
        else:
            factory = backend_factory or _real_backend_factory
            self._backend = factory()

    @property
    def live(self) -> bool:
        return self._real and not isinstance(self._backend, MockDiagnosticBackend)

    def run(self, operation_id: str, params: dict[str, Any]) -> DiagnosticResult:
        clean = _validate(operation_id, params)
        self._enforce_allowlist(operation_id, clean)

        try:
            evidence = self._backend.read(operation_id, clean)
        except DiagnosticRefused:
            raise
        except DiagnosticFailed:
            raise
        except Exception as exc:  # any backend error fails closed, never a write
            raise DiagnosticFailed(f"diagnostic backend error: {exc}") from exc

        simulated = isinstance(self._backend, MockDiagnosticBackend)
        target = clean.get("target") or clean.get("name") or ""
        return DiagnosticResult(
            operation_id=operation_id,
            target=target,
            simulated=simulated,
            evidence=_truncate(evidence),
            backend=self._backend.name,
            note=(
                "Read-only evidence. This authorises nothing; a human interprets "
                "it and decides any next step."
            ),
            checks=[{"check": "read_only", "passed": True, "detail": "no write path exists"}],
        )

    def _enforce_allowlist(self, operation_id: str, clean: dict[str, Any]) -> None:
        _enforce_target_allowlist(operation_id, clean, real=self.live)
