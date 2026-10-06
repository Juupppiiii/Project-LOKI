"""Turn a ticket plus a matched procedure into an approvable plan.

The planner is where "AI owns steps, not tickets" becomes concrete: every step
gets its own risk, owner and policy, and adjacent steps are grouped into
approval chunks that never cross a risk boundary.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from . import action_catalog
from . import risk as risk_policy
from . import _system2
from .domain import (
    Chunk,
    ExecutionPolicy,
    Owner,
    RiskClass,
    RunState,
    StepRun,
    StepState,
    TaskRun,
    new_id,
)


def _chunk_label(steps: list[StepRun]) -> str:
    """Name a chunk after what it actually does, for the approval card."""

    if not steps:
        return "empty"

    if all(s.risk is RiskClass.R0 for s in steps):
        base = "Read-only checks"
    elif all(s.risk in (RiskClass.R0, RiskClass.R1) for s in steps):
        base = "Discovery and reporting"
    elif len(steps) == 1:
        base = steps[0].description or steps[0].action
    else:
        base = f"{steps[0].action} … {steps[-1].action}"

    if steps[0].owner is Owner.HUMAN:
        base = f"Human: {base}"

    span = (
        f"step {steps[0].sequence}"
        if len(steps) == 1
        else f"steps {steps[0].sequence}–{steps[-1].sequence}"
    )
    return f"{base} ({span})"


def _group_into_chunks(steps: list[StepRun]) -> list[Chunk]:
    """Group adjacent steps that share an owner and approval policy.

    Breaks a group whenever ownership changes, the policy changes, or the step
    needs individual approval. That is what stops a low-risk approval from
    smuggling in a higher-risk action.
    """

    chunks: list[Chunk] = []
    current: list[StepRun] = []

    def flush() -> None:
        if not current:
            return
        chunk_id = new_id("chk")
        risks = [s.risk for s in current]
        chunk = Chunk(
            chunk_id=chunk_id,
            label=_chunk_label(current),
            step_ids=[s.step_id for s in current],
            risk=risk_policy.highest(risks),
            owner=current[0].owner,
            policy=current[0].policy,
        )
        for s in current:
            s.chunk_id = chunk_id
        chunks.append(chunk)
        current.clear()

    for step in steps:
        if step.policy is ExecutionPolicy.APPROVE_STEP:
            flush()
            current.append(step)
            flush()
            continue

        if current and (
            current[0].owner is not step.owner or current[0].policy is not step.policy
        ):
            flush()

        current.append(step)

    flush()
    return chunks


def build_plan(
    ticket: dict[str, Any],
    match: Any,
    alternatives: list[Any] | None = None,
    mode: str = "simulation",
) -> TaskRun:
    """Expand a System 2 Match into a persistent, per-step plan.

    ``mode`` is the execution mode the run will carry (``simulation`` by
    default). It is purely a label here; whether a live-mode run can actually
    reach a real connector is enforced later by the gateway, which refuses to
    dispatch unless its own mode matches the envelope's.
    """

    procedure = match.procedure
    params = dict(match.parameters)

    steps: list[StepRun] = []
    for i, step in enumerate(procedure.steps, start=1):
        risk, owner, policy, reason = risk_policy.classify(step)
        steps.append(
            StepRun(
                step_id=new_id("stp"),
                sequence=i,
                action=step.action,
                description=_system2.fill(step.description, params) or step.action,
                rendered=_system2.render_step(step, params),
                risk=risk,
                owner=owner,
                policy=policy,
                risk_reason=reason,
            )
        )

    chunks = _group_into_chunks(steps)

    ticket_id = str(
        ticket.get("number") or ticket.get("ticket_id") or new_id("tkt")
    )

    run = TaskRun(
        run_id=new_id("run"),
        ticket_id=ticket_id,
        ticket=ticket,
        procedure_id=procedure.procedure_id,
        procedure_name=procedure.name,
        procedure_version=_system2.procedure_version(procedure),
        assignment_group=str(ticket.get("assignment_group", "") or ""),
        state=RunState.PROPOSED,
        mode=mode,
        steps=steps,
        chunks=chunks,
        parameters=params,
        missing_parameters=list(match.missing),
        match_score=float(match.score),
        matched_keywords=list(match.matched_keywords),
        alternatives=[
            {
                "procedure_id": alt.procedure.procedure_id,
                "procedure_name": alt.procedure.name,
                "score": float(alt.score),
                "matched_keywords": list(alt.matched_keywords),
            }
            for alt in (alternatives or [])
            if alt.procedure.procedure_id != procedure.procedure_id
        ],
    )

    recompute_state(run)
    return run


def rerender(run: TaskRun, procedure: Any) -> None:
    """Re-render steps after parameters change.

    Any edit changes the rendered text, which changes the binding digest and so
    invalidates prior approvals. That is the intended behaviour.
    """

    for step_run, step in zip(run.steps, procedure.steps):
        step_run.description = (
            _system2.fill(step.description, run.parameters) or step.action
        )
        step_run.rendered = _system2.render_step(step, run.parameters)


def recompute_state(run: TaskRun) -> None:
    """Derive the run state from its steps. The steps are the source of truth."""

    from .domain import TERMINAL_STEP_STATES

    if run.state in (RunState.CANCELLED, RunState.FAILED):
        return

    if any(s.state is StepState.FAILED for s in run.steps):
        run.state = RunState.FAILED
        return

    if all(s.state in TERMINAL_STEP_STATES for s in run.steps):
        run.state = RunState.COMPLETED
        return

    if run.missing_parameters:
        run.state = RunState.NEEDS_PARAMETERS
        return

    nxt = run.next_actionable()
    if nxt is None:
        run.state = RunState.COMPLETED
        return

    if nxt.owner is Owner.HUMAN:
        run.state = RunState.PAUSED_FOR_HUMAN
        return

    if nxt.state is StepState.READY:
        run.state = RunState.READY
        return

    run.state = RunState.AWAITING_APPROVAL


def binding_digest(run: TaskRun, chunk: Chunk) -> str:
    """Pin an approval to exactly what the reviewer saw.

    Covers the procedure version, the chunk's steps and their rendered text, the
    resolved parameters and the target. Change any of those and the digest moves,
    so the old approval can no longer authorise execution.
    """

    steps = [run.step(sid) for sid in chunk.step_ids]
    payload = {
        "binding_digest_version": 2,
        "action_catalog_version": action_catalog.catalogue_version(),
        "run_id": run.run_id,
        "ticket_id": run.ticket_id,
        "procedure_id": run.procedure_id,
        "procedure_version": run.procedure_version,
        "chunk_id": chunk.chunk_id,
        "parameters": run.parameters,
        "steps": [
            {
                "step_id": s.step_id,
                "sequence": s.sequence,
                "action": s.action,
                "action_binding": action_catalog.resolve(s.action).binding(),
                "rendered": s.rendered,
                "risk": s.risk.value,
                "owner": s.owner.value,
                "policy": s.policy.value,
            }
            for s in steps
            if s is not None
        ],
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]
