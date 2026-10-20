"""Simulation worker: executes dispatched steps off a queue.

Where this fits
---------------
The Step Functions ``RunWorkflow`` state machine routes an ``ai``-owned step to
the ``DispatchSimulationAction`` task, which drops a message on the Simulation
queue and *waits for a task token*. Something has to pick that message up, run
the step, and hand the token back. That something is this worker.

Crucially, the worker does NOT contain a second execution path. It calls the
exact same seam the API uses -- ``OverlayService.execute_chunk`` -- which runs
through the default-deny ``ActionGateway`` in simulation mode, claims the
idempotency key before dispatch, writes the audit trail, and persists the
result. The worker only adds: pull from a queue, map the message to its chunk,
and report the outcome back to the orchestrator.

Safety properties inherited for free (not reimplemented here):

* Simulation-only -- ``ActionGateway`` refuses to construct in any other mode,
  and this worker refuses to start in any other mode.
* Exactly-once effect -- ``execute_chunk`` claims the idempotency key; a
  redelivered message replays the stored outcome instead of running twice.
* Default-deny -- human-owned / R3 / RX / unresolved steps are refused by the
  gateway; the worker surfaces that as a task failure for a human to pick up.

Concurrency
-----------
Decision for this build: a single always-on worker. To make that safe even if
two worker tasks are ever running, the loop holds a database-backed named lease
(the same primitive the live feed uses); only the lease holder consumes. This
is belt-and-braces -- the idempotency claim already prevents double execution --
but it keeps ordering clean on the FIFO queue.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any

from .coordination import Lease, new_lease_owner
from .domain import DomainError, StepState
from .service import OverlayService
from .worker_transport import TaskQueue, TaskReporter, WorkItem

LEASE_NAME = "simulation-worker"
DEFAULT_LEASE_TTL_SECONDS = int(os.getenv("BUCKY_WORKER_LEASE_TTL_SECONDS", "120"))
DEFAULT_WAIT_SECONDS = int(os.getenv("BUCKY_WORKER_WAIT_SECONDS", "20"))
DEFAULT_BATCH = int(os.getenv("BUCKY_WORKER_BATCH", "1"))


@dataclass
class ProcessResult:
    """Outcome of processing a single WorkItem (for logging/tests)."""

    status: str  # "succeeded" | "failed" | "refused"
    step_state: str
    summary: str = ""
    detail: str = ""


class SimulationWorker:
    """Consumes dispatched steps and reports results back to the orchestrator.

    ``service`` executes the work; ``queue`` supplies it; ``reporter`` receives
    the outcome. All three are injected so the whole worker runs offline against
    the in-memory transport in tests, and against SQS + Step Functions in prod.
    """

    def __init__(
        self,
        service: OverlayService,
        queue: TaskQueue,
        reporter: TaskReporter,
        *,
        lease_ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
        wait_seconds: int = DEFAULT_WAIT_SECONDS,
        batch_size: int = DEFAULT_BATCH,
        actor: str = "agent",
    ) -> None:
        self.service = service
        self.store = service.store
        self.queue = queue
        self.reporter = reporter
        self.actor = actor

        self._lease_ttl = max(1, int(lease_ttl_seconds))
        self._wait_seconds = max(0, int(wait_seconds))
        self._batch_size = max(1, int(batch_size))
        # Renew well inside the TTL so a slow cycle never drops leadership.
        self._renew_interval = max(1.0, self._lease_ttl / 3)

        self._owner_id = new_lease_owner(LEASE_NAME)
        self._lease: Lease | None = None

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lifecycle_lock = threading.Lock()

        # Lightweight counters for observability.
        self.processed = 0
        self.succeeded = 0
        self.failed = 0
        self.last_error: str | None = None

    # -- per-item processing (pure, synchronous, trivially testable) -----

    def process_item(self, item: WorkItem) -> ProcessResult:
        """Execute one dispatched step and report its outcome.

        Returns a ProcessResult describing what happened. Never raises for an
        expected execution outcome (refusal / needs-human / failed step) -- those
        become a reported task failure. It only lets truly unexpected errors
        propagate so the queue can redrive and eventually DLQ.
        """

        envelope = item.envelope
        run_id = envelope.get("run_id") or item.run_id
        chunk_id = envelope.get("chunk_id")
        step_id = envelope.get("step_id") or item.step_id

        if not run_id or not chunk_id or not step_id:
            # Malformed message: nothing we can safely execute. Fail the token so
            # the workflow stops waiting; the message will DLQ after redrive.
            detail = "message missing run_id/chunk_id/step_id"
            self._report_failure(item, "InvalidMessage", detail)
            self.last_error = detail
            return ProcessResult("failed", "", detail=detail)

        triggered_by = envelope.get("triggered_by") or self.actor

        # The single sanctioned execution seam. Idempotent: a redelivered message
        # replays the stored step outcome instead of dispatching again.
        exec_error: str | None = None
        try:
            self.service.execute_chunk(run_id, chunk_id, actor=triggered_by)
        except DomainError as exc:
            # A guard refused the (re-)run: concurrent claim in flight, plan
            # changed, out of order, or -- importantly -- the approval was already
            # consumed by a prior successful execution. We do NOT decide the
            # outcome from the exception; we re-read the step's persisted state
            # below, because a redelivery of completed work lands here with the
            # step already SUCCEEDED and must still report success.
            exec_error = str(exc)

        # Re-read the authoritative persisted state after execution.
        run = self.service.get_run(run_id)
        step = run.step(step_id)
        step_state = step.state.value if step is not None else ""

        if step is not None and step.state is StepState.SUCCEEDED:
            summary = step.outcome or "step executed (simulated)"
            self._report_success(
                item,
                {
                    "run_id": run_id,
                    "chunk_id": chunk_id,
                    "step_id": step_id,
                    "state": step_state,
                    "outcome": summary,
                    "simulated": True,
                    "replayed": exec_error is not None,
                },
            )
            self.succeeded += 1
            return ProcessResult("succeeded", step_state, summary=summary)

        if exec_error is not None:
            # Refused and the step is not already succeeded -> a real refusal a
            # human / retry owns.
            self._report_failure(item, "ExecutionRefused", exec_error)
            self.last_error = exec_error
            return ProcessResult("refused", step_state, detail=exec_error)

        # Executed but the step is not a success (NEEDS_HUMAN, FAILED, BLOCKED,
        # or missing): hand the token back as a failure with the state.
        detail = f"step did not complete as agent (state={step_state or 'unknown'})"
        error = "StepNeedsHuman" if step_state == StepState.NEEDS_HUMAN.value else "StepNotSucceeded"
        self._report_failure(item, error, detail)
        self.failed += 1
        return ProcessResult("failed", step_state, detail=detail)

    def _report_success(self, item: WorkItem, output: dict[str, Any]) -> None:
        if item.task_token:
            self.reporter.report_success(item.task_token, output)

    def _report_failure(self, item: WorkItem, error: str, cause: str) -> None:
        if item.task_token:
            self.reporter.report_failure(item.task_token, error, cause)

    # -- draining (used by tests and the loop body) ----------------------

    def drain_once(self) -> int:
        """Receive one batch, process each item, delete handled ones.

        Returns the number of items processed. An item that raises an unexpected
        error is left undeleted so the queue can redeliver and ultimately DLQ it.
        """

        items = self.queue.receive(self._batch_size, self._wait_seconds)
        handled = 0
        for item in items:
            try:
                self.process_item(item)
            except Exception as exc:  # unexpected: do NOT delete; let it redrive
                self.last_error = f"unexpected worker failure: {exc}"
                continue
            self.queue.delete(item)
            self.processed += 1
            handled += 1
        return handled

    # -- lease-guarded background loop (modelled on LiveFeed) ------------

    def _ensure_leader(self) -> bool:
        if self._lease is not None:
            renewed = self.store.renew_named_lease(
                LEASE_NAME, self._owner_id, self._lease.fencing_token, self._lease_ttl
            )
            if renewed is not None:
                self._lease = renewed
                return True
            self._lease = None
        acquired = self.store.try_acquire_named_lease(
            LEASE_NAME, self._owner_id, self._lease_ttl
        )
        self._lease = acquired
        return acquired is not None

    def _release_lease(self) -> bool:
        lease = self._lease
        if lease is None:
            return False
        released = self.store.release_named_lease(
            LEASE_NAME, self._owner_id, lease.fencing_token
        )
        self._lease = None
        return released

    def _loop(self) -> None:
        try:
            while not self._stop.is_set():
                if not self._ensure_leader():
                    self._stop.wait(self._renew_interval)
                    continue
                try:
                    self.drain_once()
                except Exception as exc:  # never let the loop die
                    self.last_error = f"unexpected drain failure: {exc}"
                    self._stop.wait(1.0)
        finally:
            self._release_lease()

    def start(self) -> bool:
        with self._lifecycle_lock:
            if self._thread and self._thread.is_alive():
                return False
            self._thread = None
            self._stop = threading.Event()
            thread = threading.Thread(
                target=self._loop, name="overlay-simulation-worker", daemon=True
            )
            self._thread = thread
            thread.start()
            return True

    def stop(self, timeout: float = 35.0) -> bool:
        with self._lifecycle_lock:
            thread = self._thread
            if not (thread and thread.is_alive()):
                self._thread = None
                self._release_lease()
                return False
            self._stop.set()
        thread.join(timeout=timeout)
        with self._lifecycle_lock:
            if thread.is_alive():
                return False
            if self._thread is thread:
                self._thread = None
            self._release_lease()
            return True
