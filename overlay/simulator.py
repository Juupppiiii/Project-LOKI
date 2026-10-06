"""Step execution -- SIMULATION ONLY.

This is the seam where real connectors will eventually plug in. Today it
produces simulated outcomes and touches nothing. Two properties are deliberate:

* There is no network, filesystem or credential access anywhere in this module.
* `System2.live_execute` is never imported, so the disabled live path stays
  disabled; this module does not route around it.

When connectors do arrive they belong behind `execute_step`, gated on an
allowlisted (connector, operation) pair, with the approval check that
`service.py` already performs left exactly where it is.
"""

from __future__ import annotations

from typing import Any

from .domain import Owner, RiskClass, StepRun

# Flipping this does nothing on its own. Real execution requires connectors,
# credentials, rollback and a security review -- a separate, deliberate project.
CONNECTORS_ENABLED = False

_SIM_OUTCOMES = {
    "check": "condition satisfied",
    "connect": "session opened",
    "notify": "work note posted to ticket",
    "restart_service": "service restarted, status Running",
}


class ExecutionRefused(Exception):
    """Raised when policy forbids executing a step. Never a bug -- a control."""


def execute_step(step: StepRun, parameters: dict[str, Any]) -> str:
    """Simulate one step and return its outcome.

    Refuses anything the AI is not allowed to run, even if a caller asks. The
    API also checks this; defence in depth is intentional.
    """

    if step.owner is not Owner.AI:
        raise ExecutionRefused(
            f"step {step.sequence} is owned by a human and cannot be executed by the agent"
        )

    if step.risk in (RiskClass.R3, RiskClass.RX):
        raise ExecutionRefused(
            f"step {step.sequence} is classified {step.risk.value} "
            f"({step.risk.label}) and is not eligible for agent execution"
        )

    if CONNECTORS_ENABLED:  # pragma: no cover - not reachable in this build
        raise RuntimeError(
            "Connectors are not implemented. Real execution requires an "
            "allowlisted connector gateway, credential management, "
            "postcondition checks and rollback."
        )

    detail = _SIM_OUTCOMES.get(step.action, "step acknowledged")
    target = parameters.get("server") or parameters.get("hostname") or "target"
    return f"[SIM] {detail} ({target})"


def unresolved_tokens(step: StepRun) -> bool:
    """True if the rendered step still shows an unfilled <param?> placeholder."""

    return "<" in step.rendered and "?>" in step.rendered
