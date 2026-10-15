"""Compensation / rollback for executed steps.

Pairs with ``verification.py``. Verification asks "did the action achieve its
goal?"; this asks "if it did NOT, can we safely undo what it did?". When a real
action's postcondition cannot be confirmed, leaving the system in an unknown
half-changed state is the dangerous outcome -- so a step that declares a
compensating action can have it attempted before the step is handed to a human.

Outcomes
--------
* ``compensated``   -- a rollback was defined and ran successfully; the step's
                       effect is undone (or simulated as undone).
* ``no_rollback``   -- the step declares no rollback. Nothing to undo; the step
                       still stops for a human, now with that fact recorded.
* ``failed``        -- a rollback was attempted but could not be confirmed. This
                       is the worst case and is surfaced loudly: the system may
                       be in a partially-changed state a human must inspect.

Safety properties
-----------------
* Simulation rollback is pure: it records what WOULD be compensated, touches
  nothing. This proves the seam end to end without side effects.
* Real rollback (dormant until a live connector is reviewed) plugs in behind the
  same interface and must FAIL CLOSED -- any error or ambiguity yields
  ``failed``, never ``compensated``. A compensation that merely *claims* success
  is not trusted any more than an action that does.
* Rollback never runs automatically for a human-owned or unclassified step; it
  only applies to an agent step whose own forward action was permitted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .connectors import ActionEnvelope, ActionResult
from .domain import StepRun


class RollbackStatus(str, Enum):
    COMPENSATED = "compensated"
    NO_ROLLBACK = "no_rollback"
    FAILED = "failed"


@dataclass(frozen=True)
class RollbackOutcome:
    status: RollbackStatus
    detail: str
    checks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def compensated(self) -> bool:
        return self.status is RollbackStatus.COMPENSATED

    @property
    def needs_attention(self) -> bool:
        """A failed rollback means a possible half-changed state -- escalate."""

        return self.status is RollbackStatus.FAILED

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "detail": self.detail,
            "checks": self.checks,
        }


def has_rollback(step: StepRun) -> bool:
    return bool((getattr(step, "rollback", "") or "").strip())


def compensate(
    step: StepRun,
    envelope: ActionEnvelope | None,
    result: ActionResult | None,
) -> RollbackOutcome:
    """Attempt to undo an executed step's effect.

    Routing mirrors verification:
      * no declared rollback            -> ``no_rollback``
      * simulated result                -> pure "would compensate" record
      * real result (non-simulated)     -> ``failed`` (no live compensator is
                                           registered yet; fail closed)
    """

    rb = (getattr(step, "rollback", "") or "").strip()
    if not rb:
        return RollbackOutcome(
            RollbackStatus.NO_ROLLBACK,
            f"step {step.sequence} declares no rollback; nothing to compensate",
            [{"check": "has_rollback", "passed": False, "detail": "no rollback text"}],
        )

    simulated = result is None or result.simulated
    if simulated:
        return RollbackOutcome(
            RollbackStatus.COMPENSATED,
            f"[SIM] would compensate step {step.sequence}: {rb}",
            [
                {"check": "has_rollback", "passed": True, "detail": rb[:200]},
                {"check": "simulated", "passed": True, "detail": "no real effect to undo"},
            ],
        )

    # A real forward effect with no registered live compensator: do not pretend
    # it was undone. Fail closed so a human inspects a possible half-changed state.
    return RollbackOutcome(
        RollbackStatus.FAILED,
        f"step {step.sequence} made a real change but no live rollback is "
        "registered; manual inspection required",
        [{"check": "has_live_compensator", "passed": False, "detail": "none registered"}],
    )
