"""Repository tests. SQLite always runs; PostgreSQL runs only when a test
database is reachable via BUCKY_TEST_POSTGRES_DSN, otherwise it is skipped.

The same test bodies run against both backends so the adapters stay in parity.
"""

from __future__ import annotations

import os

import pytest

from overlay import _system2, planner
from overlay.domain import (
    Approval,
    DictionaryEntry,
    KnowledgeSource,
    SourceKind,
    new_id,
)

pytestmark = pytest.mark.repository


def _make_sqlite(sqlite_path):
    from overlay.store import Store

    return Store(sqlite_path)


def _make_postgres(dsn):
    from overlay.postgres_store import PostgresStore

    store = PostgresStore(dsn, min_size=1, max_size=2, auto_migrate=True)
    # Start from a clean slate so parity assertions are deterministic.
    with store.connection() as connection:
        for table in (
            "action_executions", "audit_events", "approvals", "runs",
            "dictionary_entries", "procedure_drafts", "published_procedures",
            "knowledge_sources", "live_tickets", "settings", "credentials",
            "named_leases",
        ):
            connection.execute(f"TRUNCATE {table} CASCADE")
    return store


@pytest.fixture(params=["sqlite", "postgres"])
def repo(request, sqlite_path):
    backend = request.param
    if backend == "sqlite":
        store = _make_sqlite(sqlite_path)
    else:
        dsn = os.getenv("BUCKY_TEST_POSTGRES_DSN")
        if not dsn:
            pytest.skip("set BUCKY_TEST_POSTGRES_DSN to run PostgreSQL repository tests")
        store = _make_postgres(dsn)
    try:
        yield store
    finally:
        store.close()


def _build_run(ticket):
    procedures = _system2.load_procedures()
    match = _system2.match_ticket(ticket, procedures)
    assert match is not None, "restart ticket should match a procedure"
    return planner.build_plan(ticket, match)


def test_run_round_trips(repo, restart_ticket):
    run = _build_run(restart_ticket)
    repo.save_run(run)
    loaded = repo.get_run(run.run_id)
    assert loaded is not None
    assert loaded.run_id == run.run_id
    assert loaded.procedure_id == run.procedure_id
    assert len(loaded.steps) == len(run.steps)
    assert len(loaded.chunks) == len(run.chunks)


def test_active_run_lookup_and_count(repo, restart_ticket):
    run = _build_run(restart_ticket)
    repo.save_run(run)
    assert repo.count_runs_by_ticket(run.ticket_id) == 1
    active = repo.find_active_run_by_ticket(run.ticket_id)
    assert active is not None and active.run_id == run.run_id


def test_approval_supersede_keeps_one_active(repo, restart_ticket):
    run = _build_run(restart_ticket)
    repo.save_run(run)
    chunk = run.chunks[0]

    def _approval():
        return Approval(
            approval_id=new_id("apr"),
            run_id=run.run_id,
            chunk_id=chunk.chunk_id,
            step_ids=list(chunk.step_ids),
            binding_digest=planner.binding_digest(run, chunk),
            decision="approved",
            actor="approver",
        )

    repo.supersede_approvals(run.run_id, chunk.chunk_id, "2020-01-01T00:00:00+00:00")
    repo.save_approval(_approval())
    repo.supersede_approvals(run.run_id, chunk.chunk_id, "2020-01-02T00:00:00+00:00")
    repo.save_approval(_approval())

    active = repo.active_approval(run.run_id, chunk.chunk_id)
    assert active is not None
    assert len(repo.list_approvals(run.run_id)) == 2  # both recorded, one active


def test_audit_is_append_only_and_ordered(repo, restart_ticket):
    run = _build_run(restart_ticket)
    repo.save_run(run)
    repo.append_audit(run.run_id, "run.proposed", "alice")
    repo.append_audit(run.run_id, "chunk.approved", "bob")
    events = repo.list_audit(run.run_id)
    assert [e.event for e in events] == ["run.proposed", "chunk.approved"]


def test_execution_claim_is_idempotent(repo, restart_ticket):
    run = _build_run(restart_ticket)
    repo.save_run(run)
    envelope = {
        "idempotency_key": "exec:test-key",
        "run_id": run.run_id,
        "chunk_id": run.chunks[0].chunk_id,
        "step_id": run.steps[0].step_id,
        "approval_id": "apr-x",
        "action": "check",
        "action_version": "1.0.0",
        "catalog_version": "sha256:test",
        "connector": "simulation",
        "operation": "observe",
    }
    claimed_first, _ = repo.claim_action_execution(envelope)
    claimed_second, existing = repo.claim_action_execution(envelope)
    assert claimed_first is True
    assert claimed_second is False
    assert existing["status"] == "claimed"

    repo.complete_action_execution("exec:test-key", "[SIM] ok")
    assert repo.get_action_execution("exec:test-key")["status"] == "succeeded"


def test_dictionary_entry_round_trips(repo):
    entry = DictionaryEntry(
        entry_id=new_id("dict"),
        source_id="src-1",
        canonical="account locked",
        aliases=["cannot sign in", "locked out"],
    )
    repo.save_dictionary_entry(entry)
    loaded = repo.list_dictionary(only_enabled=True)
    assert any(e.entry_id == entry.entry_id for e in loaded)


def test_source_cascade_removes_derived_rows(repo):
    source = KnowledgeSource(
        source_id=new_id("src"),
        filename="dict.csv",
        kind=SourceKind.DICTIONARY,
        file_format="csv",
        size_bytes=10,
        sha256="abc123",
        uploaded_by="alice",
    )
    repo.save_source(source, content="canonical,aliases\naccount locked,locked out")
    repo.save_dictionary_entry(
        DictionaryEntry(new_id("dict"), source.source_id, "account locked", ["locked out"])
    )
    result = repo.delete_source_cascade(source.source_id)
    assert result["sources"] == 1
    assert result["dictionary_entries"] >= 1
    assert repo.get_source(source.source_id) is None


def test_readiness_reports_backend_and_ready(repo):
    status = repo.readiness()
    assert status["ready"] is True
    assert status["backend"] in ("sqlite", "postgres")
