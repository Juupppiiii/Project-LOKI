"""Transport seam for the simulation worker.

The worker's job -- take a dispatched step, run it through the existing
execution path, and tell the orchestrator whether it succeeded -- is the same
whether the queue is AWS SQS and the orchestrator is Step Functions, or an
in-memory fake used by a test. This module defines that seam so the worker loop
in ``worker.py`` never imports boto3 and can be exercised fully offline.

Three small things:

* ``WorkItem`` -- one unit of work pulled off the queue. It mirrors exactly the
  message body that the Step Functions ``DispatchSimulationAction`` task sends
  (``taskToken``, ``runId``, ``stepId``, ``envelope``), plus an opaque
  ``receipt`` the queue implementation uses to delete the message later.

* ``TaskQueue`` -- receive a batch, delete one when done. A FIFO queue in
  production; a list in a test.

* ``TaskReporter`` -- report success/failure/heartbeat for a task token. Step
  Functions ``SendTaskSuccess``/``SendTaskFailure``/``SendTaskHeartbeat`` in
  production; a recorder in a test.

The in-memory implementations here are not test-only helpers bolted on; they are
the reference implementation of the contract and are what makes the worker
runnable without any AWS wiring.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class WorkItem:
    """One dispatched step, as delivered by the queue.

    ``task_token`` is the Step Functions callback token (empty when there is no
    orchestrator, e.g. a local drain). ``envelope`` is the full
    ``ActionEnvelope.to_dict()`` payload; it already carries ``run_id``,
    ``chunk_id`` and ``step_id``, which is what the worker executes against.
    ``receipt`` is opaque queue bookkeeping (an SQS receipt handle, or a list
    index for the in-memory queue).
    """

    task_token: str
    run_id: str
    step_id: str
    envelope: dict[str, Any]
    receipt: Any = None

    @staticmethod
    def from_message(body: dict[str, Any], receipt: Any = None) -> "WorkItem":
        """Build a WorkItem from the Step Functions message body.

        The producer sends camelCase ``runId``/``stepId``; the envelope itself
        uses snake_case. We read ids from the envelope when present (it is the
        authoritative, approval-bound copy) and fall back to the message keys.
        """

        envelope = dict(body.get("envelope") or {})
        run_id = str(envelope.get("run_id") or body.get("runId") or "")
        step_id = str(envelope.get("step_id") or body.get("stepId") or "")
        return WorkItem(
            task_token=str(body.get("taskToken") or ""),
            run_id=run_id,
            step_id=step_id,
            envelope=envelope,
            receipt=receipt,
        )


class TaskQueue(Protocol):
    """A source of WorkItems that can acknowledge completion."""

    def receive(self, max_items: int, wait_seconds: int) -> list[WorkItem]:
        """Return up to ``max_items`` items, long-polling up to ``wait_seconds``."""
        ...

    def delete(self, item: WorkItem) -> None:
        """Acknowledge an item so it is not redelivered."""
        ...


class TaskReporter(Protocol):
    """Where the worker reports the outcome of a task token."""

    def report_success(self, task_token: str, output: dict[str, Any]) -> None: ...

    def report_failure(self, task_token: str, error: str, cause: str) -> None: ...

    def heartbeat(self, task_token: str) -> None: ...


# ---------------------------------------------------------------------------
# Reference / in-memory implementations (used locally and by tests)
# ---------------------------------------------------------------------------


class InMemoryQueue:
    """A thread-safe in-memory FIFO queue implementing ``TaskQueue``.

    ``put`` enqueues a message body (the same dict Step Functions would send).
    ``receive`` hands out WorkItems; delivered-but-not-deleted items become
    visible again only after ``make_visible`` is called, which lets a test model
    redelivery after a crash. ``delete`` removes an item permanently.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: list[dict[str, Any]] = []
        self._inflight: dict[int, dict[str, Any]] = {}
        self._next_id = 0

    def put(self, body: dict[str, Any]) -> None:
        with self._lock:
            self._pending.append(dict(body))

    def receive(self, max_items: int, wait_seconds: int) -> list[WorkItem]:
        # No blocking: the fake is deterministic and returns immediately with
        # whatever is pending. ``wait_seconds`` is accepted for protocol parity.
        del wait_seconds
        out: list[WorkItem] = []
        with self._lock:
            while self._pending and len(out) < max_items:
                body = self._pending.pop(0)
                receipt = self._next_id
                self._next_id += 1
                self._inflight[receipt] = body
                out.append(WorkItem.from_message(body, receipt=receipt))
        return out

    def delete(self, item: WorkItem) -> None:
        with self._lock:
            self._inflight.pop(item.receipt, None)

    def make_visible(self, item: WorkItem) -> None:
        """Return an in-flight (received, not deleted) item to the queue."""
        with self._lock:
            body = self._inflight.pop(item.receipt, None)
            if body is not None:
                self._pending.insert(0, body)

    @property
    def depth(self) -> int:
        with self._lock:
            return len(self._pending)

    @property
    def inflight(self) -> int:
        with self._lock:
            return len(self._inflight)


@dataclass
class ReportedOutcome:
    task_token: str
    kind: str  # "success" | "failure" | "heartbeat"
    output: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    cause: str = ""


class RecordingReporter:
    """A ``TaskReporter`` that records calls instead of calling AWS."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.outcomes: list[ReportedOutcome] = []

    def report_success(self, task_token: str, output: dict[str, Any]) -> None:
        with self._lock:
            self.outcomes.append(
                ReportedOutcome(task_token, "success", output=dict(output))
            )

    def report_failure(self, task_token: str, error: str, cause: str) -> None:
        with self._lock:
            self.outcomes.append(
                ReportedOutcome(task_token, "failure", error=error, cause=cause)
            )

    def heartbeat(self, task_token: str) -> None:
        with self._lock:
            self.outcomes.append(ReportedOutcome(task_token, "heartbeat"))

    # Convenience accessors for tests.
    def successes(self) -> list[ReportedOutcome]:
        with self._lock:
            return [o for o in self.outcomes if o.kind == "success"]

    def failures(self) -> list[ReportedOutcome]:
        with self._lock:
            return [o for o in self.outcomes if o.kind == "failure"]
