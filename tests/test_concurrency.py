"""Concurrency tests: exactly-once execution claims and single-poller leadership.

Multiple Store connections over one SQLite file stand in for multiple processes.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from overlay.store import Store

pytestmark = [pytest.mark.concurrency, pytest.mark.integration]


def _envelope(key):
    return {
        "idempotency_key": key,
        "run_id": "run-1",
        "chunk_id": "chunk-1",
        "step_id": "step-1",
        "approval_id": "apr-1",
        "action": "check",
        "action_version": "1.0.0",
        "catalog_version": "sha256:test",
        "connector": "simulation",
        "operation": "observe",
    }


def test_sequential_reclaim_is_idempotent(sqlite_path):
    """The core exactly-once guarantee: a repeated claim never wins twice.

    This is the invariant execution safety depends on, and it holds on SQLite
    without relying on writer-contention timing.
    """

    store = Store(sqlite_path)
    try:
        first, _ = store.claim_action_execution(_envelope("exec:seq-key"))
        second, existing = store.claim_action_execution(_envelope("exec:seq-key"))
        assert first is True
        assert second is False
        assert existing["status"] == "claimed"
    finally:
        store.close()


def test_concurrent_claims_grant_exactly_one_winner(sqlite_path):
    """Concurrent claims for one key yield exactly one winner and one claim row.

    SQLite serialises writers, so this uses a modest thread count matching its
    single-process design; the winner still commits exactly one row. The heavy
    many-writer stress race is exercised against PostgreSQL where it belongs.
    """

    stores = [Store(sqlite_path) for _ in range(6)]
    barrier = threading.Barrier(len(stores))
    results = []
    lock = threading.Lock()

    def attempt(store):
        barrier.wait()
        claimed, _ = store.claim_action_execution(_envelope("exec:race-key"))
        with lock:
            results.append(claimed)

    threads = [threading.Thread(target=attempt, args=(s,)) for s in stores]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    record = stores[0].get_action_execution("exec:race-key")
    for s in stores:
        s.close()

    assert results.count(True) == 1, results
    assert results.count(False) == len(stores) - 1, results
    assert record is not None and record["status"] == "claimed"


@pytest.mark.postgres
def test_high_contention_claims_on_postgres():
    """Many concurrent processes, one winner, no duplicate side effect.

    PostgreSQL is the concurrent backend; this proves the claim under real
    write concurrency. Skipped unless BUCKY_TEST_POSTGRES_DSN is set.
    """

    dsn = __import__("os").getenv("BUCKY_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set BUCKY_TEST_POSTGRES_DSN to run the high-contention race")

    from overlay.postgres_store import PostgresStore

    stores = [PostgresStore(dsn, min_size=1, max_size=2, auto_migrate=True) for _ in range(8)]
    with stores[0].connection() as connection:
        connection.execute("DELETE FROM action_executions WHERE idempotency_key = 'exec:pg-race'")
    barrier = threading.Barrier(len(stores))
    results = []
    lock = threading.Lock()

    def attempt(store):
        barrier.wait()
        claimed, _ = store.claim_action_execution(_envelope("exec:pg-race"))
        with lock:
            results.append(claimed)

    threads = [threading.Thread(target=attempt, args=(s,)) for s in stores]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for s in stores:
        s.close()

    assert results.count(True) == 1, results
    assert results.count(False) == len(stores) - 1, results


def test_two_supervisors_yield_a_single_poller_then_failover(sqlite_path, monkeypatch):
    import overlay.live as live

    monkeypatch.setattr(
        live, "load_credentials",
        lambda: {"instance": "t", "username": "u", "password": "p", "present": True},
    )

    store_a = Store(sqlite_path)
    store_b = Store(sqlite_path)
    cfg = {**live.DEFAULT_CONFIG, "enabled": True, "interval_seconds": 30}
    store_a.set_setting(live.SETTINGS_KEY, cfg)

    calls = []
    feed_a = live.LiveFeed(SimpleNamespace(store=store_a))
    feed_b = live.LiveFeed(SimpleNamespace(store=store_b))
    feed_a.reader = SimpleNamespace(fetch=lambda cfg, creds: calls.append("a") or [])
    feed_b.reader = SimpleNamespace(fetch=lambda cfg, creds: calls.append("b") or [])
    feed_a._renew_interval = 1
    feed_b._renew_interval = 1

    try:
        feed_a.start()
        feed_b.start()
        time.sleep(2)

        leaders = [f for f in (feed_a, feed_b) if f.status()["leader_here"]]
        assert len(leaders) == 1, [f.status() for f in (feed_a, feed_b)]
        leader = leaders[0]
        standby = feed_b if leader is feed_a else feed_a
        leader_label = "a" if leader is feed_a else "b"
        assert set(calls) == {leader_label}, calls

        assert leader.stop() is True
        time.sleep(2)
        assert standby.status()["leader_here"] is True
    finally:
        feed_a.stop()
        feed_b.stop()
        store_a.close()
        store_b.close()

    # After failover the standby polled at least once.
    assert len(set(calls)) == 2, calls
