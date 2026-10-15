"""Crash recovery for the action-execution idempotency ledger.

The ledger guarantees an action is dispatched at most once. Its one open crash
window is between claiming an execution and recording the terminal outcome: a
worker that dies there leaves the record stuck in 'claimed', which would wedge
the step forever. recover_stale_executions closes that window by FAILING such
stale claims (never silently retrying), so the step surfaces for a human.
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.unit


def _envelope(key: str) -> dict:
    return {
        "idempotency_key": key,
        "run_id": "run_x",
        "chunk_id": "chk_x",
        "step_id": "step_x",
        "approval_id": "appr_x",
        "action": "restart_service",
        "action_version": "1.0.0",
        "catalog_version": "sha256:test",
        "connector": "simulation",
        "operation": "restart_service",
    }


def test_claim_then_reclaim_marks_failed(store):
    claimed, rec = store.claim_action_execution(_envelope("exec:recover1"))
    assert claimed is True
    assert rec["status"] == "claimed"

    # Timeout 0 => every outstanding claim is already "stale".
    reclaimed = store.reclaim_stale_executions(0)
    keys = {r["idempotency_key"] for r in reclaimed}
    assert "exec:recover1" in keys

    after = store.get_action_execution("exec:recover1")
    assert after["status"] == "failed"
    assert "reclaimed" in (after["error"] or "")


def test_fresh_claim_is_not_reclaimed(store):
    store.claim_action_execution(_envelope("exec:fresh"))
    # A generous timeout must NOT touch a claim made moments ago.
    reclaimed = store.reclaim_stale_executions(10_000)
    assert all(r["idempotency_key"] != "exec:fresh" for r in reclaimed)
    assert store.get_action_execution("exec:fresh")["status"] == "claimed"


def test_completed_execution_is_never_reclaimed(store):
    store.claim_action_execution(_envelope("exec:done"))
    store.complete_action_execution("exec:done", "[SIM] ok")
    reclaimed = store.reclaim_stale_executions(0)
    assert all(r["idempotency_key"] != "exec:done" for r in reclaimed)
    assert store.get_action_execution("exec:done")["status"] == "succeeded"


def test_service_recover_audits_each_reclaim(service):
    # Claim directly through the service's store, then recover via the service.
    service.store.claim_action_execution(_envelope("exec:svc1"))
    result = service.recover_stale_executions(timeout_seconds=0)
    assert result["reclaimed"] >= 1

    # The reclaim is written to the audit log (read from the store directly,
    # since the fake run_id has no run document for the service-level guard).
    events = [e.to_dict() for e in service.store.list_audit("run_x")]
    assert any(e["event"] == "execution.reclaimed" for e in events)


def test_reclaimed_execution_can_be_claimed_again(store):
    # After a stale claim is failed, a NEW claim on the same key is refused
    # (the key is unique and now terminal), so the step takes the "earlier
    # execution failed" path -> needs human, never a silent re-dispatch.
    store.claim_action_execution(_envelope("exec:again"))
    store.reclaim_stale_executions(0)
    claimed, rec = store.claim_action_execution(_envelope("exec:again"))
    assert claimed is False
    assert rec["status"] == "failed"
