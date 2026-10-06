"""Connector contract and approval-bound execution envelope.

The connector protocol is intentionally narrower than StepRun. A connector
receives one versioned, allowlisted operation with validated parameters; it
never receives arbitrary procedure text, a shell command, or a secret.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from .action_catalog import ActionDefinition, catalogue_version
from .domain import Approval, Chunk, StepRun, TaskRun, now_iso


@dataclass(frozen=True)
class ActionEnvelope:
    run_id: str
    chunk_id: str
    step_id: str
    approval_id: str
    approval_digest: str
    action: str
    action_version: str
    catalog_version: str
    connector: str
    operation: str
    target_ref: str
    parameters: dict[str, Any]
    risk: str
    actor: str
    triggered_by: str
    mode: str
    issued_at: str
    idempotency_key: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ActionResult:
    summary: str
    connector: str
    operation: str
    simulated: bool
    evidence: dict[str, Any] = field(default_factory=dict)


class Connector(Protocol):
    def execute(self, envelope: ActionEnvelope) -> ActionResult: ...


class ConnectorRefused(Exception):
    """The envelope is invalid or policy forbids connector dispatch."""


class ConnectorFailure(Exception):
    """The connector attempted work but could not establish a safe outcome."""


def _target_ref(run: TaskRun, definition: ActionDefinition) -> str:
    if definition.operation == "post_work_note":
        return f"servicenow:ticket/{run.ticket_id}"
    target = (
        run.parameters.get("server")
        or run.parameters.get("hostname")
        or run.ticket.get("cmdb_ci")
        or "target"
    )
    return f"cmdb:{target}"


def _selected_parameters(
    run: TaskRun, definition: ActionDefinition
) -> dict[str, Any]:
    return {
        name: run.parameters[name]
        for name in definition.parameter_names
        if name in run.parameters and run.parameters[name] not in (None, "")
    }


def _idempotency_key(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "exec:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_envelope(
    run: TaskRun,
    chunk: Chunk,
    step: StepRun,
    approval: Approval,
    definition: ActionDefinition,
    triggered_by: str,
) -> ActionEnvelope:
    parameters = _selected_parameters(run, definition)
    stable = {
        "run_id": run.run_id,
        "chunk_id": chunk.chunk_id,
        "step_id": step.step_id,
        "approval_digest": approval.binding_digest,
        "action": definition.action,
        "action_version": definition.version,
        "catalog_version": catalogue_version(),
        "connector": definition.connector,
        "operation": definition.operation,
        "target_ref": _target_ref(run, definition),
        "parameters": parameters,
    }
    return ActionEnvelope(
        **stable,
        approval_id=approval.approval_id,
        risk=step.risk.value,
        actor="agent",
        triggered_by=triggered_by,
        mode=run.mode,
        issued_at=now_iso(),
        idempotency_key=_idempotency_key(stable),
    )


def contains_unresolved(value: Any) -> bool:
    """Reject placeholders anywhere in nested connector inputs."""

    if isinstance(value, str):
        return "<" in value and "?>" in value
    if isinstance(value, dict):
        return any(contains_unresolved(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(contains_unresolved(v) for v in value)
    return False
