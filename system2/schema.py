"""System 2 data model: the contract between discovery (System 1) and the
execution engine (System 2).

A *procedure* is a structured, reusable resolution: a trigger that says which
tickets it applies to, the parameters it needs, and the ordered steps to run.
Procedures are stored as JSON so they are human-readable, reviewable and
version-controllable -- they ARE the "rules" the engine executes.

Nothing in this module executes anything. It only defines and validates the
shape of a procedure.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Action verbs the engine understands. Kept small and explicit; the engine
# maps each to a simulated behaviour. Adding a real behaviour later must not
# change this vocabulary.
KNOWN_ACTIONS = {
    "connect",        # establish a (simulated) session to a target host
    "run_command",    # run a shell/PowerShell command
    "restart_service",# restart a named service
    "reboot",         # reboot the target host
    "check",          # verify a condition; may gate success
    "delete_profile", # remove a user profile
    "notify",         # post a note / message
    "manual",         # a step a human must perform (not automatable yet)
}

AUTOMATABILITY_LEVELS = {"high", "medium", "low", "manual"}


@dataclass
class Parameter:
    """A value the procedure needs, and where to get it from a ticket."""

    name: str
    type: str = "string"          # hostname | string | username | service ...
    source: str | None = None      # ticket field to read, e.g. "cmdb_ci"
    required: bool = True
    description: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Parameter":
        return cls(
            name=d["name"],
            type=d.get("type", "string"),
            source=d.get("source"),
            required=d.get("required", True),
            description=d.get("description", ""),
        )


@dataclass
class Step:
    """One ordered action in a procedure."""

    action: str
    description: str = ""
    command: str | None = None
    target: str | None = None
    service: str | None = None
    expect: str | None = None       # condition for a "check" step
    rollback: str | None = None     # optional rollback command (for later)

    def validate(self, errors: list[str]) -> None:
        if self.action not in KNOWN_ACTIONS:
            errors.append(
                f"unknown action '{self.action}' "
                f"(known: {sorted(KNOWN_ACTIONS)})"
            )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Step":
        return cls(
            action=d["action"],
            description=d.get("description", ""),
            command=d.get("command"),
            target=d.get("target"),
            service=d.get("service"),
            expect=d.get("expect"),
            rollback=d.get("rollback"),
        )


@dataclass
class Trigger:
    """How the engine decides a ticket matches this procedure."""

    keywords: list[str] = field(default_factory=list)
    assignment_group: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Trigger":
        return cls(
            keywords=[k.lower() for k in d.get("keywords", [])],
            assignment_group=d.get("assignment_group"),
        )


@dataclass
class Procedure:
    """A full structured resolution procedure."""

    procedure_id: str
    name: str
    trigger: Trigger
    steps: list[Step]
    parameters: list[Parameter] = field(default_factory=list)
    automatability: str = "medium"
    frequency: int = 0
    source_tickets: list[str] = field(default_factory=list)
    notes: str = ""

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.procedure_id:
            errors.append("missing procedure_id")
        if not self.steps:
            errors.append(f"{self.procedure_id}: no steps")
        if self.automatability not in AUTOMATABILITY_LEVELS:
            errors.append(
                f"{self.procedure_id}: bad automatability "
                f"'{self.automatability}'"
            )
        for step in self.steps:
            step.validate(errors)
        return errors

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Procedure":
        return cls(
            procedure_id=d["procedure_id"],
            name=d.get("name", d["procedure_id"]),
            trigger=Trigger.from_dict(d.get("trigger", {})),
            steps=[Step.from_dict(s) for s in d.get("steps", [])],
            parameters=[Parameter.from_dict(p) for p in d.get("parameters", [])],
            automatability=d.get("automatability", "medium"),
            frequency=d.get("frequency", 0),
            source_tickets=d.get("source_tickets", []),
            notes=d.get("notes", ""),
        )


def load_procedures(path: str | Path) -> list[Procedure]:
    """Load and validate all procedures from a JSON file.

    Raises ValueError with a combined message if any procedure is invalid, so
    a malformed rule set fails loudly rather than silently misbehaving.
    """

    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))

    raw = data["procedures"] if isinstance(data, dict) else data
    procedures = [Procedure.from_dict(p) for p in raw]

    all_errors: list[str] = []
    for proc in procedures:
        all_errors.extend(proc.validate())

    if all_errors:
        raise ValueError(
            "Invalid procedures.json:\n  " + "\n  ".join(all_errors)
        )

    return procedures
