"""Risk classification policy.

The existing procedures.json carries no per-step risk metadata, so the overlay
derives it here from the abstract action vocabulary. Two rules make that safe:

1. **Default deny.** Anything not explicitly classified becomes RX, which can
   never be executed by the AI. Silence is not permission.
2. **A step may be classified explicitly.** If a procedure step gains a
   `risk` / `owner` / `policy` field later, that wins over the action default.
   Upgrading a procedure is therefore additive, not a rewrite.

`run_command` is deliberately RX. A read-only `Get-PSDrive` and a destructive
`Remove-Item` share the same action, so the action alone cannot justify
execution. Those steps route to a human until someone classifies them.
"""

from __future__ import annotations

from typing import Any

from . import action_catalog
from .domain import ExecutionPolicy, Owner, RiskClass

# action -> (risk, owner, policy, reason)
_ACTION_POLICY: dict[str, tuple[RiskClass, Owner, ExecutionPolicy, str]] = {
    "check": (
        RiskClass.R0,
        Owner.AI,
        ExecutionPolicy.APPROVE_CHUNK,
        "Verification only; reads state without changing it.",
    ),
    "connect": (
        RiskClass.R0,
        Owner.AI,
        ExecutionPolicy.APPROVE_CHUNK,
        "Opens a session; no state change on its own.",
    ),
    "notify": (
        RiskClass.R1,
        Owner.AI,
        ExecutionPolicy.APPROVE_CHUNK,
        "Additive ticket note; reversible and low impact.",
    ),
    "restart_service": (
        RiskClass.R2,
        Owner.AI,
        ExecutionPolicy.APPROVE_STEP,
        "Interrupts a running service; needs its own approval.",
    ),
    "reboot": (
        RiskClass.R3,
        Owner.HUMAN,
        ExecutionPolicy.MANUAL_ONLY,
        "Takes a host offline; human-only in this phase.",
    ),
    "delete_profile": (
        RiskClass.R3,
        Owner.HUMAN,
        ExecutionPolicy.MANUAL_ONLY,
        "Destroys user data; human-only in this phase.",
    ),
    "run_command": (
        RiskClass.RX,
        Owner.HUMAN,
        ExecutionPolicy.MANUAL_ONLY,
        "Arbitrary command: the action cannot prove it is non-destructive. "
        "Classify the step explicitly to make it automatable.",
    ),
    "manual": (
        RiskClass.R3,
        Owner.HUMAN,
        ExecutionPolicy.MANUAL_ONLY,
        "Declared as human work by the procedure author.",
    ),
}

_UNKNOWN = (
    RiskClass.RX,
    Owner.HUMAN,
    ExecutionPolicy.MANUAL_ONLY,
    "Unrecognised action; blocked by default-deny policy.",
)

# Risk levels the AI may ever execute in this phase, regardless of policy.
AI_ELIGIBLE_RISKS = {RiskClass.R0, RiskClass.R1, RiskClass.R2}

_RISK_ORDER = [RiskClass.R0, RiskClass.R1, RiskClass.R2, RiskClass.R3, RiskClass.RX]


def classify(step: Any) -> tuple[RiskClass, Owner, ExecutionPolicy, str]:
    """Classify a System 2 Step.

    Explicit metadata on the step wins; otherwise the action default applies.
    """

    explicit_risk = getattr(step, "risk", None)
    action = getattr(step, "action", "") or ""

    risk, owner, policy, reason = _ACTION_POLICY.get(action, _UNKNOWN)

    if explicit_risk:
        try:
            risk = RiskClass(str(explicit_risk).upper())
            reason = f"Explicitly classified as {risk.value} by the procedure."
            # An explicit low risk still has to name its owner/policy; default
            # to AI + chunk approval only for the risks the AI may touch.
            if risk in AI_ELIGIBLE_RISKS:
                owner = Owner.AI
                policy = (
                    ExecutionPolicy.APPROVE_STEP
                    if risk is RiskClass.R2
                    else ExecutionPolicy.APPROVE_CHUNK
                )
            else:
                owner, policy = Owner.HUMAN, ExecutionPolicy.MANUAL_ONLY
        except ValueError:
            risk, owner, policy, reason = _UNKNOWN

    explicit_owner = getattr(step, "owner", None)
    if explicit_owner and str(explicit_owner).lower() == "human":
        owner, policy = Owner.HUMAN, ExecutionPolicy.MANUAL_ONLY
        reason += " Procedure pins this step to a human."

    # Procedure metadata can narrow authority but cannot widen the immutable
    # action catalogue. Raw commands and destructive legacy actions stay human
    # even if an uploaded procedure claims a low risk.
    try:
        definition = action_catalog.resolve(action)
    except action_catalog.UnknownAction:
        risk, owner, policy, reason = _UNKNOWN
    else:
        if not definition.ai_eligible:
            owner, policy = Owner.HUMAN, ExecutionPolicy.MANUAL_ONLY
            reason += (
                f" Action {definition.reference} is pinned human-only by the "
                "versioned action catalogue."
            )

    # Belt and braces: risk the AI may not touch is never AI-owned.
    if risk not in AI_ELIGIBLE_RISKS:
        owner, policy = Owner.HUMAN, ExecutionPolicy.MANUAL_ONLY

    return risk, owner, policy, reason


def highest(risks: list[RiskClass]) -> RiskClass:
    """The most restrictive risk in a group. Used to label a chunk."""

    if not risks:
        return RiskClass.RX
    return max(risks, key=lambda r: _RISK_ORDER.index(r))


def describe_policy() -> list[dict[str, str]]:
    """Expose the policy table so the UI can show it instead of hardcoding it."""

    rows = [
        {
            "action": action,
            "risk": risk.value,
            "risk_label": risk.label,
            "owner": owner.value,
            "policy": policy.value,
            "reason": reason,
        }
        for action, (risk, owner, policy, reason) in sorted(_ACTION_POLICY.items())
    ]
    return rows
