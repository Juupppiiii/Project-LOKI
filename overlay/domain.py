"""Pure domain model for the task overlay.

Deliberately free of framework, database and transport concerns. Nothing here
imports FastAPI, sqlite3 or the System 2 modules, so the same model can back a
browser UI, a desktop app, a CLI or a chat bot without modification.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def now_iso() -> str:
    """UTC timestamp. Stored as ISO-8601 so it is portable across stores."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Risk, ownership and policy
# ---------------------------------------------------------------------------


class RiskClass(str, Enum):
    """How much damage a single step could do.

    Assigned by risk.py from explicit policy -- never guessed from free text.
    """

    R0 = "R0"  # read-only observation, no production state change
    R1 = "R1"  # low impact, additive or reversible
    R2 = "R2"  # real operational change, controlled scope, recoverable
    R3 = "R3"  # high impact, destructive or hard to reverse
    RX = "RX"  # unknown / unclassifiable -> never automated

    @property
    def label(self) -> str:
        return {
            "R0": "read-only",
            "R1": "low impact",
            "R2": "operational change",
            "R3": "high impact",
            "RX": "unclassified",
        }[self.value]


class Owner(str, Enum):
    AI = "ai"
    HUMAN = "human"


class ExecutionPolicy(str, Enum):
    """What has to happen before a step may run."""

    APPROVE_CHUNK = "approve_chunk"  # approved as part of a grouped chunk
    APPROVE_STEP = "approve_step"    # needs its own individual approval
    MANUAL_ONLY = "manual_only"      # a human performs it; AI never executes


# ---------------------------------------------------------------------------
# State machines
# ---------------------------------------------------------------------------


class RunState(str, Enum):
    PROPOSED = "proposed"
    NEEDS_PARAMETERS = "needs_parameters"
    AWAITING_APPROVAL = "awaiting_approval"
    READY = "ready"
    RUNNING = "running"
    PAUSED_FOR_HUMAN = "paused_for_human"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


TERMINAL_RUN_STATES = {
    RunState.COMPLETED,
    RunState.FAILED,
    RunState.CANCELLED,
}


class StepState(str, Enum):
    PENDING = "pending"
    BLOCKED = "blocked"
    AWAITING_APPROVAL = "awaiting_approval"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NEEDS_HUMAN = "needs_human"
    SKIPPED = "skipped"


TERMINAL_STEP_STATES = {
    StepState.SUCCEEDED,
    StepState.FAILED,
    StepState.SKIPPED,
}


# ---------------------------------------------------------------------------
# Core records
# ---------------------------------------------------------------------------


@dataclass
class StepRun:
    """One step of a plan, with its own risk, owner and lifecycle."""

    step_id: str
    sequence: int
    action: str
    description: str
    rendered: str
    risk: RiskClass
    owner: Owner
    policy: ExecutionPolicy
    chunk_id: str | None = None
    state: StepState = StepState.PENDING
    outcome: str = ""
    evidence: str = ""
    completed_by: str | None = None
    completed_at: str | None = None
    # Why the classifier landed here. Shown in the UI so a reviewer can
    # disagree with the policy rather than trusting a bare label.
    risk_reason: str = ""

    @property
    def ai_executable(self) -> bool:
        return self.owner is Owner.AI and self.policy is not ExecutionPolicy.MANUAL_ONLY

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["risk"] = self.risk.value
        d["risk_label"] = self.risk.label
        d["owner"] = self.owner.value
        d["policy"] = self.policy.value
        d["state"] = self.state.value
        d["ai_executable"] = self.ai_executable
        return d


@dataclass
class Chunk:
    """A contiguous group of steps approved together.

    Chunks never span a risk boundary: a single approval must not quietly
    authorise a higher-risk action than the reviewer saw.
    """

    chunk_id: str
    label: str
    step_ids: list[str]
    risk: RiskClass
    owner: Owner
    policy: ExecutionPolicy

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "label": self.label,
            "step_ids": list(self.step_ids),
            "risk": self.risk.value,
            "risk_label": self.risk.label,
            "owner": self.owner.value,
            "policy": self.policy.value,
        }


@dataclass
class Approval:
    """An immutable record that a human authorised a specific chunk.

    `binding_digest` pins the approval to exactly what was shown: procedure
    version, target, parameters and rendered steps. If any of those change the
    digest no longer matches and the approval is refused.
    """

    approval_id: str
    run_id: str
    chunk_id: str
    step_ids: list[str]
    binding_digest: str
    decision: str  # "approved" | "rejected"
    actor: str
    reason: str = ""
    created_at: str = field(default_factory=now_iso)
    consumed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AuditEvent:
    """Append-only. Never updated, never deleted."""

    event_id: str
    run_id: str
    event: str
    actor: str
    detail: dict[str, Any] = field(default_factory=dict)
    step_id: str | None = None
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskRun:
    """A ticket being worked through a procedure, step by step."""

    run_id: str
    ticket_id: str
    ticket: dict[str, Any]
    procedure_id: str
    procedure_name: str
    procedure_version: str
    assignment_group: str
    state: RunState
    steps: list[StepRun]
    chunks: list[Chunk]
    parameters: dict[str, Any] = field(default_factory=dict)
    missing_parameters: list[str] = field(default_factory=list)
    match_score: float = 0.0
    matched_keywords: list[str] = field(default_factory=list)
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    mode: str = "simulation"
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    # -- navigation ------------------------------------------------------

    def step(self, step_id: str) -> StepRun | None:
        return next((s for s in self.steps if s.step_id == step_id), None)

    def chunk(self, chunk_id: str) -> Chunk | None:
        return next((c for c in self.chunks if c.chunk_id == chunk_id), None)

    def next_actionable(self) -> StepRun | None:
        """The first step that is not finished. Order is authoritative."""

        return next(
            (s for s in self.steps if s.state not in TERMINAL_STEP_STATES),
            None,
        )

    def next_chunk(self) -> Chunk | None:
        """The chunk containing the next unfinished step."""

        step = self.next_actionable()
        if step is None or step.chunk_id is None:
            return None
        return self.chunk(step.chunk_id)

    # -- derived reporting ----------------------------------------------

    @property
    def ai_step_count(self) -> int:
        return sum(1 for s in self.steps if s.ai_executable)

    @property
    def human_step_count(self) -> int:
        return len(self.steps) - self.ai_step_count

    @property
    def completed_step_count(self) -> int:
        return sum(1 for s in self.steps if s.state in TERMINAL_STEP_STATES)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "ticket_id": self.ticket_id,
            "ticket": self.ticket,
            "procedure_id": self.procedure_id,
            "procedure_name": self.procedure_name,
            "procedure_version": self.procedure_version,
            "assignment_group": self.assignment_group,
            "state": self.state.value,
            "steps": [s.to_dict() for s in self.steps],
            "chunks": [c.to_dict() for c in self.chunks],
            "parameters": self.parameters,
            "missing_parameters": list(self.missing_parameters),
            "match_score": self.match_score,
            "matched_keywords": list(self.matched_keywords),
            "alternatives": self.alternatives,
            "mode": self.mode,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "ai_step_count": self.ai_step_count,
            "human_step_count": self.human_step_count,
            "completed_step_count": self.completed_step_count,
            "total_step_count": len(self.steps),
        }


class DomainError(Exception):
    """A rule was violated. The API maps this to 4xx, never a 500."""


class DuplicateRunError(DomainError):
    """This ticket already has a run that is still in progress.

    Not a hard block: re-running a procedure on the same ticket can be
    legitimate (a fix did not hold, or a second pass is needed). The caller must
    say so explicitly, so a duplicate is always a decision rather than an
    accident.
    """

    def __init__(self, ticket_id: str, run_id: str, run_state: str) -> None:
        self.ticket_id = ticket_id
        self.existing_run_id = run_id
        self.existing_state = run_state
        super().__init__(
            f"{ticket_id} already has a run in progress "
            f"({run_state.replace('_', ' ')}). Continue that one, or create a "
            f"second run deliberately."
        )


# ---------------------------------------------------------------------------
# Customisation: uploaded knowledge (handbooks + dictionaries)
# ---------------------------------------------------------------------------


class SourceKind(str, Enum):
    """What an uploaded file contributes."""

    HANDBOOK = "handbook"      # becomes procedure drafts (how to do the work)
    DICTIONARY = "dictionary"  # becomes matching aliases (what words mean)


class DraftStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    REJECTED = "rejected"


@dataclass
class KnowledgeSource:
    """An uploaded file. Immutable once stored; re-upload creates a new source."""

    source_id: str
    filename: str
    kind: SourceKind
    file_format: str
    size_bytes: int
    sha256: str
    uploaded_by: str
    status: str = "parsed"                      # parsed | failed
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    draft_count: int = 0
    entry_count: int = 0
    uploaded_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d


@dataclass
class ProcedureDraft:
    """A candidate procedure parsed from an upload.

    Never loaded by the engine while it is a draft. Publishing is the human
    review gate, and it is the only path into the live rule set.
    """

    draft_id: str
    source_id: str
    procedure_id: str
    name: str
    payload: dict[str, Any]
    status: DraftStatus = DraftStatus.DRAFT
    validation_errors: list[str] = field(default_factory=list)
    risk_warnings: list[str] = field(default_factory=list)
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    review_note: str = ""
    created_at: str = field(default_factory=now_iso)

    @property
    def publishable(self) -> bool:
        return not self.validation_errors and self.status is DraftStatus.DRAFT

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["publishable"] = self.publishable
        return d


@dataclass
class DictionaryEntry:
    """A term and the ways people actually write it.

    Used only to widen *matching*. A dictionary can never grant execution
    rights, so this is the safest customisation surface in the system.
    """

    entry_id: str
    source_id: str
    canonical: str
    aliases: list[str] = field(default_factory=list)
    note: str = ""
    enabled: bool = True
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Live ticket intake
# ---------------------------------------------------------------------------


class LiveTicketStatus(str, Enum):
    NEW = "new"            # fetched, not yet triaged
    PROPOSED = "proposed"  # a run was created from it
    IGNORED = "ignored"    # dismissed by a human
    NO_MATCH = "no_match"  # no procedure applies; stays with a human


@dataclass
class LiveTicket:
    """A ticket pulled from a live source. Read-only mirror; never written back."""

    live_id: str
    ticket_number: str
    sys_id: str
    source: str
    table: str
    payload: dict[str, Any]
    status: LiveTicketStatus = LiveTicketStatus.NEW
    match: dict[str, Any] = field(default_factory=dict)
    run_id: str | None = None
    fetched_at: str = field(default_factory=now_iso)
    sys_updated_on: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d
