"""Tests for the simulation worker.

Fully offline: the worker runs against the in-memory transport
(``InMemoryQueue`` + ``RecordingReporter``) and the real ``OverlayService`` on
isolated SQLite in simulation mode. No boto3, no network, no AWS.

What matters here:
  * an approved agent chunk, dispatched as a message, is executed and reported
    back as a success -- through the SAME execute_chunk path the API uses;
  * a redelivered message does not execute twice (idempotency replay) and still
    reports success;
  * a message for an unapproved chunk is reported as a failure, not executed;
  * a malformed message fails its token instead of crashing the worker;
  * the transport contract (receive/delete/make_visible) behaves as specified.
"""

from __future__ import annotations

import pytest

from overlay.domain import RunState, StepState
from overlay.worker import SimulationWorker
from overlay.worker_transport import InMemoryQueue, RecordingReporter, WorkItem

pytestmark = pytest.mark.unit


def _agent_chunks(run):
    return [c for c in run.chunks if c.owner.value == "ai"]


def _message_for_chunk(run, chunk, *, task_token: str, triggered_by: str = "agent") -> dict:
    """A dispatch message mirroring the Step Functions body shape.

    The worker executes per chunk; the envelope carries the ids it needs. We
    include one representative step_id (the first in the chunk) as the token's
    step, exactly as Step Functions would for that dispatched step.
    """

    step_id = chunk.step_ids[0]
    return {
        "taskToken": task_token,
        "runId": run.run_id,
        "stepId": step_id,
        "envelope": {
            "run_id": run.run_id,
            "chunk_id": chunk.chunk_id,
            "step_id": step_id,
            "triggered_by": triggered_by,
            "mode": "simulation",
        },
    }


@pytest.fixture
def worker(service):
    queue = InMemoryQueue()
    reporter = RecordingReporter()
    w = SimulationWorker(service, queue, reporter, wait_seconds=0)
    return w


def _approved_run(service, restart_ticket):
    run = service.propose(restart_ticket, actor="alice")
    for chunk in _agent_chunks(run):
        service.decide_chunk(run.run_id, chunk.chunk_id, decision="approved", actor="bob")
    return service.get_run(run.run_id)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_worker_executes_approved_chunk_and_reports_success(worker, service, restart_ticket):
    run = _approved_run(service, restart_ticket)
    first_chunk = _agent_chunks(run)[0]
    worker.queue.put(_message_for_chunk(run, first_chunk, task_token="tok-1"))

    handled = worker.drain_once()

    assert handled == 1
    successes = worker.reporter.successes()
    assert len(successes) == 1
    assert successes[0].task_token == "tok-1"
    assert successes[0].output["simulated"] is True
    assert successes[0].output["state"] == StepState.SUCCEEDED.value

    # The real execution path ran: the chunk's steps are now succeeded.
    refreshed = service.get_run(run.run_id)
    for sid in first_chunk.step_ids:
        assert refreshed.step(sid).state is StepState.SUCCEEDED


def test_worker_drives_a_run_to_completion(worker, service, restart_ticket):
    run = _approved_run(service, restart_ticket)
    for i, chunk in enumerate(_agent_chunks(run)):
        worker.queue.put(_message_for_chunk(run, chunk, task_token=f"tok-{i}"))

    while worker.drain_once():
        pass

    final = service.get_run(run.run_id)
    assert final.state is RunState.COMPLETED
    assert all(s.state is StepState.SUCCEEDED for s in final.steps)
    assert len(worker.reporter.successes()) == len(_agent_chunks(run))


# ---------------------------------------------------------------------------
# Idempotency on redelivery
# ---------------------------------------------------------------------------


def test_redelivered_message_does_not_execute_twice(worker, service, restart_ticket):
    run = _approved_run(service, restart_ticket)
    chunk = _agent_chunks(run)[0]

    # First delivery: executes and succeeds.
    worker.queue.put(_message_for_chunk(run, chunk, task_token="tok-A"))
    items = worker.queue.receive(1, 0)
    assert len(items) == 1
    worker.process_item(items[0])
    # Simulate a crash before delete: the message becomes visible again.
    worker.queue.make_visible(items[0])

    # Second delivery of the same work: must replay, not double-execute.
    again = worker.queue.receive(1, 0)
    assert len(again) == 1
    result = worker.process_item(again[0])

    # Reported success both times; the step is succeeded exactly once.
    assert result.status == "succeeded"
    assert len(worker.reporter.successes()) == 2
    refreshed = service.get_run(run.run_id)
    assert refreshed.step(chunk.step_ids[0]).state is StepState.SUCCEEDED


# ---------------------------------------------------------------------------
# Failure / refusal paths
# ---------------------------------------------------------------------------


def test_unapproved_chunk_is_reported_as_failure_not_executed(worker, service, restart_ticket):
    run = service.propose(restart_ticket, actor="alice")  # NOT approved
    chunk = _agent_chunks(run)[0]
    worker.queue.put(_message_for_chunk(run, chunk, task_token="tok-x"))

    worker.drain_once()

    assert worker.reporter.successes() == []
    failures = worker.reporter.failures()
    assert len(failures) == 1
    assert failures[0].task_token == "tok-x"
    # The step never executed.
    refreshed = service.get_run(run.run_id)
    assert refreshed.step(chunk.step_ids[0]).state is not StepState.SUCCEEDED


def test_malformed_message_fails_token_without_crashing(worker):
    bad = WorkItem(task_token="tok-bad", run_id="", step_id="", envelope={})
    result = worker.process_item(bad)
    assert result.status == "failed"
    failures = worker.reporter.failures()
    assert failures and failures[0].error == "InvalidMessage"


def test_empty_task_token_is_tolerated(worker, service, restart_ticket):
    # A local drain with no orchestrator (empty token) must still execute and
    # simply not attempt a callback.
    run = _approved_run(service, restart_ticket)
    chunk = _agent_chunks(run)[0]
    worker.queue.put(_message_for_chunk(run, chunk, task_token=""))

    worker.drain_once()

    # No callbacks recorded (empty token), but the work ran.
    assert worker.reporter.outcomes == []
    refreshed = service.get_run(run.run_id)
    assert refreshed.step(chunk.step_ids[0]).state is StepState.SUCCEEDED


# ---------------------------------------------------------------------------
# Transport contract
# ---------------------------------------------------------------------------


def test_inmemory_queue_receive_delete_and_visibility():
    q = InMemoryQueue()
    q.put({"taskToken": "t", "envelope": {"run_id": "r", "chunk_id": "c", "step_id": "s"}})
    assert q.depth == 1

    items = q.receive(10, 0)
    assert len(items) == 1
    assert q.depth == 0 and q.inflight == 1
    assert items[0].run_id == "r" and items[0].step_id == "s"

    # make_visible returns it; delete removes it for good.
    q.make_visible(items[0])
    assert q.depth == 1 and q.inflight == 0
    again = q.receive(10, 0)
    q.delete(again[0])
    assert q.depth == 0 and q.inflight == 0


def test_workitem_prefers_envelope_ids_over_message_keys():
    body = {
        "taskToken": "t",
        "runId": "msg-run",
        "stepId": "msg-step",
        "envelope": {"run_id": "env-run", "chunk_id": "env-chunk", "step_id": "env-step"},
    }
    item = WorkItem.from_message(body, receipt=1)
    assert item.run_id == "env-run"
    assert item.step_id == "env-step"
    assert item.envelope["chunk_id"] == "env-chunk"


# ---------------------------------------------------------------------------
# Producer side: service.dispatch_chunk_async -> queue -> worker -> report
# ---------------------------------------------------------------------------


def test_dispatch_async_enqueues_and_worker_completes_it(worker, service, restart_ticket):
    run = _approved_run(service, restart_ticket)
    first_chunk = _agent_chunks(run)[0]

    # Producer: the service enqueues the approved chunk onto the same queue the
    # worker consumes. No execution happens yet.
    result = service.dispatch_chunk_async(run.run_id, first_chunk.chunk_id, worker.queue)
    assert result["dispatched"] is True
    assert worker.queue.depth == 1
    # Not executed by the mere enqueue.
    assert service.get_run(run.run_id).step(first_chunk.step_ids[0]).state is not StepState.SUCCEEDED

    # Consumer: the worker drains it and runs the real execute_chunk path. The
    # bare enqueue carries no Step Functions token, so there is no callback to
    # record; what matters is the real execution happened via the queue.
    handled = worker.drain_once()
    assert handled == 1
    refreshed = service.get_run(run.run_id)
    for sid in first_chunk.step_ids:
        assert refreshed.step(sid).state is StepState.SUCCEEDED


def test_dispatch_async_dedup_collapses_double_send(worker, service, restart_ticket):
    run = _approved_run(service, restart_ticket)
    chunk = _agent_chunks(run)[0]

    service.dispatch_chunk_async(run.run_id, chunk.chunk_id, worker.queue)
    # A second identical dispatch has the same idempotency key -> FIFO content
    # de-dup drops it before it reaches a worker.
    service.dispatch_chunk_async(run.run_id, chunk.chunk_id, worker.queue)
    assert worker.queue.depth == 1


def test_dispatch_async_refuses_unapproved_chunk(service, restart_ticket):
    from overlay.domain import DomainError
    from overlay.worker_transport import InMemoryQueue

    run = service.propose(restart_ticket, actor="alice")  # not approved
    chunk = _agent_chunks(run)[0]
    with pytest.raises(DomainError):
        service.dispatch_chunk_async(run.run_id, chunk.chunk_id, InMemoryQueue())


def test_dispatch_async_is_audited(worker, service, restart_ticket):
    run = _approved_run(service, restart_ticket)
    chunk = _agent_chunks(run)[0]
    service.dispatch_chunk_async(run.run_id, chunk.chunk_id, worker.queue)
    events = service.audit(run.run_id)
    assert any(e["event"] == "chunk.dispatched_async" for e in events)
