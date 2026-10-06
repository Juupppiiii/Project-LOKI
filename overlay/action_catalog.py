"""Immutable, versioned action catalogue for the execution boundary.

Procedures still use their existing action names so the API and UI remain
compatible. Before execution, each name resolves to one exact implementation
version and one allowlisted (connector, operation) pair. Unknown actions fail
closed. Future live connectors extend this catalogue; they never accept raw
procedure text as an operation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ActionDefinition:
    action: str
    version: str
    connector: str
    operation: str
    ai_eligible: bool
    parameter_names: tuple[str, ...] = ()
    required_parameters: tuple[str, ...] = ()
    simulation_outcome: str = ""

    @property
    def reference(self) -> str:
        return f"{self.action}@{self.version}"

    def binding(self) -> dict[str, object]:
        """Fields that approvals bind to and connectors must enforce."""

        return {
            "reference": self.reference,
            "connector": self.connector,
            "operation": self.operation,
            "ai_eligible": self.ai_eligible,
            "parameter_names": list(self.parameter_names),
            "required_parameters": list(self.required_parameters),
        }


# Version 1 freezes the behavior of the existing action vocabulary. Human-only
# actions are catalogued so plans remain explainable, but the gateway refuses
# to dispatch them. Live connector definitions will use distinct connector and
# operation values after their own review; changing one requires a new version.
_ACTIONS = (
    ActionDefinition(
        "check", "1.0.0", "simulation", "observe", True,
        ("server", "hostname", "service_name", "profile_user"),
        simulation_outcome="condition satisfied",
    ),
    ActionDefinition(
        "connect", "1.0.0", "simulation", "open_session", True,
        ("server", "hostname"),
        simulation_outcome="session opened",
    ),
    ActionDefinition(
        "notify", "1.0.0", "simulation", "post_work_note", True,
        ("server", "hostname"),
        simulation_outcome="work note posted to ticket",
    ),
    ActionDefinition(
        "restart_service", "1.0.0", "simulation", "restart_service", True,
        ("server", "hostname", "service_name"), ("service_name",),
        "service restarted, status Running",
    ),
    ActionDefinition("reboot", "1.0.0", "human", "reboot", False),
    ActionDefinition("delete_profile", "1.0.0", "human", "delete_profile", False),
    ActionDefinition("run_command", "1.0.0", "human", "run_command", False),
    ActionDefinition("manual", "1.0.0", "human", "manual", False),
    # First real connector binding. Distinct from the simulated `notify` above:
    # this action is bound to the live `servicenow` connector and is only ever
    # dispatched when the gateway runs in live mode AND the ServiceNow write
    # switch is on. In every other configuration the gateway refuses it. It is
    # the lowest-risk real action available -- posting a work note cannot alter
    # infrastructure and is trivially verifiable on the ticket.
    ActionDefinition(
        "servicenow.work_note.post", "1.0.0", "servicenow", "post_work_note", True,
        ("ticket_id", "sys_id", "note"), ("note",),
        simulation_outcome="work note posted to ticket",
    ),
)

_BY_NAME = {item.action: item for item in _ACTIONS}


class UnknownAction(ValueError):
    """The catalogue cannot safely resolve an action."""


def resolve(action: str, version: str | None = None) -> ActionDefinition:
    definition = _BY_NAME.get(action)
    if definition is None:
        raise UnknownAction(f"action '{action}' is not in the versioned catalogue")
    if version is not None and version != definition.version:
        raise UnknownAction(
            f"action '{action}' version '{version}' is not available; "
            f"catalogue provides '{definition.version}'"
        )
    return definition


def catalogue_version() -> str:
    payload = [asdict(item) for item in sorted(_ACTIONS, key=lambda x: x.action)]
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def catalogue() -> tuple[ActionDefinition, ...]:
    return _ACTIONS
