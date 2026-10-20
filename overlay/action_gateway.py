"""Validating execution gateway -- simulation is the only available connector.

This module establishes the contract future AWS/SaaS connectors must satisfy.
It deliberately cannot be switched to live execution: any mode other than
``simulation`` fails at startup, and the only registered connector has no
network, filesystem, subprocess, or credential access.
"""

from __future__ import annotations

import os

from . import action_catalog
from .connectors import (
    ActionEnvelope,
    ActionResult,
    Connector,
    ConnectorRefused,
    contains_unresolved,
)
from .domain import Owner, RiskClass, StepRun


class SimulationConnector:
    """Deterministic local connector with no side effects."""

    def execute(self, envelope: ActionEnvelope) -> ActionResult:
        definition = action_catalog.resolve(envelope.action, envelope.action_version)
        target = envelope.target_ref.split(":", 1)[-1] or "target"
        return ActionResult(
            summary=f"[SIM] {definition.simulation_outcome} ({target})",
            connector=envelope.connector,
            operation=envelope.operation,
            simulated=True,
            evidence={
                "action_reference": definition.reference,
                "catalog_version": envelope.catalog_version,
                "target_ref": envelope.target_ref,
            },
        )


class ActionGateway:
    """Default-deny dispatcher for exact action/connector/operation bindings."""

    # The only modes the gateway recognises. ``simulation`` is the default and
    # touches nothing. ``live`` additionally registers reviewed real connectors,
    # but each real action is still gated to one exact allowlisted binding and a
    # per-connector write switch; everything else continues to simulate or is
    # refused. Any other mode fails at startup.
    _MODES = ("simulation", "live")

    # Actions whose catalogue binding is served by a real connector in live
    # mode. Mapping is binding -> connector name. Anything not listed here is
    # served by the simulation connector regardless of mode, so adding a real
    # connector is an explicit, reviewed allowlist edit -- never implicit.
    _LIVE_BINDINGS = {
        ("servicenow", "post_work_note"): "servicenow",
    }

    def __init__(self, mode: str | None = None) -> None:
        self.mode = (mode or os.getenv("BUCKY_EXECUTION_MODE", "simulation")).strip().lower()
        if self.mode not in self._MODES:
            raise RuntimeError(
                f"BUCKY_EXECUTION_MODE={self.mode!r} is not available; valid modes "
                "are 'simulation' (default) and 'live'"
            )
        self._connectors: dict[str, Connector] = {"simulation": SimulationConnector()}
        if self.mode == "live":
            # Register reviewed real connectors only in live mode. Import here so
            # the simulation path never imports connector dependencies.
            from .connectors_servicenow import ServiceNowConnector

            self._connectors["servicenow"] = ServiceNowConnector()

    def dispatch(self, envelope: ActionEnvelope, step: StepRun) -> ActionResult:
        definition = action_catalog.resolve(envelope.action, envelope.action_version)

        # The run's mode and the gateway's mode must agree. A simulation run can
        # never reach a live connector, and a live run is still only permitted to
        # use a real connector for an explicitly allowlisted binding (below).
        if envelope.mode != self.mode:
            raise ConnectorRefused(
                f"run mode '{envelope.mode}' does not match gateway mode '{self.mode}'"
            )

        binding = (definition.connector, definition.operation)
        is_live_binding = binding in self._LIVE_BINDINGS
        if is_live_binding and self.mode != "live":
            raise ConnectorRefused(
                f"action {definition.reference} requires live mode and is not "
                "available in this build"
            )
        if definition.connector != "simulation" and not is_live_binding:
            # A non-simulation binding that is not on the live allowlist can never
            # be dispatched. Fail closed.
            raise ConnectorRefused(
                f"action {definition.reference} has no permitted connector binding"
            )
        if step.owner is not Owner.AI:
            raise ConnectorRefused(
                f"step {step.sequence} is human-owned and cannot be connector-executed"
            )
        if step.risk in (RiskClass.R3, RiskClass.RX):
            raise ConnectorRefused(
                f"step {step.sequence} is {step.risk.value} and cannot be agent-executed"
            )
        if not definition.ai_eligible:
            raise ConnectorRefused(
                f"action {definition.reference} is not eligible for agent execution"
            )
        if (
            envelope.connector != definition.connector
            or envelope.operation != definition.operation
            or envelope.catalog_version != action_catalog.catalogue_version()
        ):
            raise ConnectorRefused("envelope does not match the pinned action catalogue")
        if not envelope.approval_id or not envelope.approval_digest:
            raise ConnectorRefused("connector dispatch requires a bound approval")
        if not envelope.idempotency_key.startswith("exec:"):
            raise ConnectorRefused("connector dispatch requires a valid idempotency key")
        if contains_unresolved(envelope.parameters) or contains_unresolved(envelope.target_ref):
            raise ConnectorRefused("connector inputs contain an unresolved parameter")

        missing = [
            name
            for name in definition.required_parameters
            if envelope.parameters.get(name) in (None, "")
        ]
        if missing:
            raise ConnectorRefused(
                "connector inputs are missing required parameters: " + ", ".join(missing)
            )

        connector = self._connectors.get(envelope.connector)
        if connector is None:
            raise ConnectorRefused(
                f"connector '{envelope.connector}' is not registered"
            )
        return connector.execute(envelope)
