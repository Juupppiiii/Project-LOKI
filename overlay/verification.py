"""Postcondition verification for executed steps.

The execution boundary's job does not end when a connector reports success. A
connector saying "service restarted" is a *claim*; LOKI must confirm the world
actually reached the desired state before it treats the step as done and moves
on. This module is that confirmation seam.

Why this exists
---------------
Executing an action and verifying its effect are different competencies, and
conflating them is how automation causes damage: a connector can return success
while the real system is unchanged (a race, a silent permission failure, a
cached response). So after every connector result, `execute_chunk` asks this
module whether the action's postcondition holds.

Outcomes
--------
* ``verified``   -- the postcondition was checked and holds. The step may be
                    marked succeeded and the plan may continue.
* ``unverified`` -- the action ran but its effect could NOT be confirmed. The
                    step must stop for a human (``NEEDS_HUMAN``); we never let a
                    plan proceed on an unconfirmed state change.
* ``skipped``    -- no postcondition is defined for this action yet. The step
                    continues, but the audit records that nothing was verified,
                    so the gap is visible rather than hidden.

Safety properties
-----------------
* Simulation checks are pure: they confirm the simulated result is internally
  consistent and well formed. They touch no network, filesystem, or process.
* Real-connector checks (dormant until a live connector is reviewed and enabled)
  belong here too, behind the same interface. The place they plug in is marked
  explicitly below. A real check re-reads the target through a read-only path
  and compares it to the intended state; it must FAIL CLOSED -- any error or
  ambiguity yields ``unverified``, never ``verified``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .action_catalog import ActionDefinition
from .connectors import ActionEnvelope, ActionResult


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class VerificationOutcome:
    status: VerificationStatus
    detail: str
    checks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True only when a postcondition was defined AND it holds.

        ``skipped`` is NOT ok-to-the-point-of-silence: callers may continue on a
        skip, but they must record it. Only ``verified`` is a positive
        confirmation of the desired state.
        """

        return self.status is VerificationStatus.VERIFIED

    @property
    def blocks(self) -> bool:
        """True when the plan must stop and hand to a human."""

        return self.status is VerificationStatus.UNVERIFIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "detail": self.detail,
            "checks": self.checks,
        }


# A postcondition check receives the executed action's definition, the envelope
# that was dispatched, and the connector's result. It returns an outcome. It
# must be side-effect-free for simulation and read-only for real connectors.
PostconditionCheck = Callable[
    [ActionDefinition, ActionEnvelope, ActionResult], VerificationOutcome
]


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"check": name, "passed": bool(passed), "detail": detail}


# ---------------------------------------------------------------------------
# Simulation postconditions (pure; no I/O)
# ---------------------------------------------------------------------------
# A simulated action has no real-world effect to inspect, so "verification"
# here confirms the result is internally consistent: it is marked simulated, it
# names the operation that was requested, and it carries the evidence the UI and
# audit rely on. This proves the seam works end to end and gives the audit a
# real verification record, without pretending a simulation changed anything.


def _verify_simulation(
    definition: ActionDefinition,
    envelope: ActionEnvelope,
    result: ActionResult,
) -> VerificationOutcome:
    checks = [
        _check(
            "result_is_simulated",
            result.simulated is True,
            "a simulation-connector result must be marked simulated",
        ),
        _check(
            "operation_matches",
            result.operation == definition.operation,
            f"result operation '{result.operation}' matches the dispatched "
            f"'{definition.operation}'",
        ),
        _check(
            "summary_present",
            bool(result.summary and result.summary.strip()),
            "the simulated outcome has a non-empty summary",
        ),
    ]
    if all(c["passed"] for c in checks):
        return VerificationOutcome(
            VerificationStatus.VERIFIED,
            f"simulated postcondition for {definition.reference} holds",
            checks,
        )
    failed = [c["check"] for c in checks if not c["passed"]]
    return VerificationOutcome(
        VerificationStatus.UNVERIFIED,
        f"simulated postcondition for {definition.reference} could not be "
        f"confirmed: {', '.join(failed)}",
        checks,
    )


# ---------------------------------------------------------------------------
# Real-connector postconditions (dormant until a live connector is enabled)
# ---------------------------------------------------------------------------
# This is where a real action's confirmation plugs in. For the ServiceNow
# work-note connector, the postcondition is "the note is present on the ticket":
# a read-only GET of the ticket's work notes, confirming the posted text appears
# and the ticket's sys_id matches. It is NOT implemented here because the live
# connector itself is gated off; wiring a half-built live verification would be
# worse than none. When that connector is reviewed and enabled, add its check to
# the registry below. Until then a real result (simulated is False) with no
# registered check returns ``unverified`` -- fail closed, never a false
# "verified".


def _verify_unregistered_real(
    definition: ActionDefinition,
    envelope: ActionEnvelope,
    result: ActionResult,
) -> VerificationOutcome:
    return VerificationOutcome(
        VerificationStatus.UNVERIFIED,
        f"a real (non-simulated) result for {definition.reference} has no "
        "registered postcondition check; refusing to assume it succeeded",
        [_check("has_real_postcondition", False, "no live check registered yet")],
    )


# Per-operation real postcondition checks. Empty today, by design: no real
# connector is enabled. A live connector adds its read-only verifier here.
_REAL_CHECKS: dict[str, PostconditionCheck] = {}


def verify(
    definition: ActionDefinition,
    envelope: ActionEnvelope,
    result: ActionResult,
) -> VerificationOutcome:
    """Confirm the postcondition of an executed action.

    Routing:
      * simulated result            -> pure consistency check (``_verify_simulation``)
      * real result, check present  -> that connector's read-only verifier
      * real result, no check       -> ``unverified`` (fail closed)
    """

    if result.simulated:
        return _verify_simulation(definition, envelope, result)

    check = _REAL_CHECKS.get(definition.operation)
    if check is None:
        return _verify_unregistered_real(definition, envelope, result)
    return check(definition, envelope, result)
