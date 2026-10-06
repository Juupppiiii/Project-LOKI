"""System 2 executor: run a matched procedure in a SAFE mode.

Execution layers:
  A) runbook  - render an ordered, parameter-filled checklist for a human.
  B) dry_run  - the engine walks the steps and prints what it WOULD do,
                with simulated results. Nothing real is touched.
  C) live     - DISABLED. Intentionally not implemented. Any attempt raises,
                so this build can never act against a real system.

The executor only understands the abstract action vocabulary from schema.py;
it has no real connectors. That is what keeps it universal and safe.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from schema import Procedure, Step


# Hard switch. This build is simulation-only by design. Turning this on does
# nothing except raise, because no live connectors exist.
LIVE_EXECUTION_ENABLED = False


@dataclass
class StepResult:
    step: Step
    rendered: str
    simulated_outcome: str = ""


@dataclass
class RunResult:
    procedure_id: str
    mode: str
    parameters: dict[str, Any]
    steps: list[StepResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ok: bool = True


def _fill(text: str | None, params: dict[str, Any]) -> str:
    """Substitute {param} tokens; leave unresolved tokens visibly marked."""

    if not text:
        return ""

    def repl(match: re.Match) -> str:
        key = match.group(1)
        return str(params.get(key, f"<{key}?>"))

    return re.sub(r"\{(\w+)\}", repl, text)


def _render_step(step: Step, params: dict[str, Any]) -> str:
    """Human-readable one-line rendering of a step with params filled in."""

    desc = _fill(step.description, params) or step.action
    detail = ""

    if step.action == "run_command" and step.command:
        detail = f"  $ {_fill(step.command, params)}"
    elif step.action == "restart_service":
        detail = f"  service: {_fill(step.service, params)}"
    elif step.action in ("connect", "reboot") and step.target:
        detail = f"  target: {_fill(step.target, params)}"
    elif step.action == "check":
        cond = _fill(step.expect, params) if step.expect else ""
        cmd = _fill(step.command, params) if step.command else ""
        detail = f"  verify: {cond}" + (f"  ({cmd})" if cmd else "")

    return f"[{step.action}] {desc}" + (f"\n{detail}" if detail else "")


def _simulate_outcome(step: Step, params: dict[str, Any]) -> str:
    """Return a plausible simulated result for a step (no real work)."""

    target = _fill(step.target or params.get("server", "target"), params)
    if step.action == "connect":
        return f"[SIM] session opened to {target}"
    if step.action == "run_command":
        return "[SIM] command completed, exit 0"
    if step.action == "restart_service":
        return f"[SIM] service {_fill(step.service, params)} restarted, status Running"
    if step.action == "reboot":
        return f"[SIM] {target} rebooted, back online"
    if step.action == "check":
        return "[SIM] condition satisfied"
    if step.action == "delete_profile":
        return "[SIM] profile removed"
    if step.action == "notify":
        return "[SIM] work note posted to ticket"
    if step.action == "manual":
        return "[SIM] awaiting human action"
    return "[SIM] step acknowledged"


def render_runbook(procedure: Procedure, params: dict[str, Any]) -> RunResult:
    """Layer A: produce a human-followable checklist. No execution."""

    result = RunResult(procedure.procedure_id, "runbook", params)
    for step in procedure.steps:
        result.steps.append(StepResult(step, _render_step(step, params)))
    return result


def dry_run(procedure: Procedure, params: dict[str, Any], missing: list[str] | None = None) -> RunResult:
    """Layer B: simulate execution step by step. No execution."""

    result = RunResult(procedure.procedure_id, "dry_run", params)

    if missing:
        result.warnings.append(
            f"Missing required parameters: {', '.join(missing)}. "
            "Steps using them will show unresolved <param?> tokens."
        )
        result.ok = False

    for step in procedure.steps:
        rendered = _render_step(step, params)
        outcome = _simulate_outcome(step, params)
        result.steps.append(StepResult(step, rendered, outcome))

    return result


def live_execute(*_args, **_kwargs):
    """Layer C: intentionally disabled in this build."""

    raise RuntimeError(
        "Live execution is disabled. This is a simulation-only build with no "
        "real connectors. To ever enable it you must add connectors, "
        "credentials, an approval gate and rollback -- a separate, deliberate "
        "project."
    )


def format_result(result: RunResult) -> str:
    """Pretty-print a RunResult for the CLI."""

    lines = []
    header = f"MODE: {result.mode.upper()}   PROCEDURE: {result.procedure_id}"
    lines.append(header)
    lines.append("=" * len(header))

    if result.parameters:
        lines.append("Parameters:")
        for k, v in result.parameters.items():
            lines.append(f"  {k} = {v}")

    for warn in result.warnings:
        lines.append(f"WARNING: {warn}")

    lines.append("")
    for i, sr in enumerate(result.steps, 1):
        lines.append(f"{i}. {sr.rendered}")
        if sr.simulated_outcome:
            lines.append(f"   -> {sr.simulated_outcome}")

    lines.append("")
    lines.append(f"Result: {'OK' if result.ok else 'INCOMPLETE (see warnings)'}")
    lines.append("(simulation only - no real system was contacted)")
    return "\n".join(lines)
