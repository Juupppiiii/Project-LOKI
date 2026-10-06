"""Unit tests for cross-process coordination primitives on SQLite.

Two Store instances over one database file stand in for two processes.
"""

from __future__ import annotations

import pytest

from overlay.store import Store

pytestmark = [pytest.mark.unit, pytest.mark.concurrency]


@pytest.fixture
def two_processes(sqlite_path):
    a = Store(sqlite_path)
    b = Store(sqlite_path)
    try:
        yield a, b
    finally:
        a.close()
        b.close()


def test_only_one_owner_holds_a_lease(two_processes):
    a, b = two_processes
    first = a.try_acquire_named_lease("live-feed", "owner-a", 30)
    assert first is not None and first.fencing_token == 1
    assert b.try_acquire_named_lease("live-feed", "owner-b", 30) is None


def test_owner_can_renew_its_own_lease(two_processes):
    a, _ = two_processes
    lease = a.try_acquire_named_lease("live-feed", "owner-a", 30)
    renewed = a.renew_named_lease("live-feed", "owner-a", lease.fencing_token, 30)
    assert renewed is not None
    assert renewed.fencing_token == lease.fencing_token


def test_non_owner_cannot_release_or_renew(two_processes):
    a, b = two_processes
    lease = a.try_acquire_named_lease("live-feed", "owner-a", 30)
    assert b.release_named_lease("live-feed", "owner-b", lease.fencing_token) is False
    assert b.renew_named_lease("live-feed", "owner-b", lease.fencing_token, 30) is None


def test_release_lets_next_owner_acquire_with_higher_token(two_processes):
    a, b = two_processes
    lease = a.try_acquire_named_lease("live-feed", "owner-a", 30)
    assert a.release_named_lease("live-feed", "owner-a", lease.fencing_token) is True
    successor = b.try_acquire_named_lease("live-feed", "owner-b", 30)
    assert successor is not None
    assert successor.fencing_token > lease.fencing_token


def test_registry_revision_advances_on_publish_across_connections(two_processes):
    a, b = two_processes
    before = b.get_registry_revision()
    a.publish_procedure("test.proc", "Test", {}, "tester")
    assert b.get_registry_revision() > before


def test_registry_revision_is_monotonic(two_processes):
    a, b = two_processes
    r0 = b.get_registry_revision()
    a.publish_procedure("test.one", "One", {}, "tester")
    r1 = b.get_registry_revision()
    a.set_published_enabled("test.one", False)
    r2 = b.get_registry_revision()
    assert r0 < r1 < r2


def test_zero_or_negative_ttl_is_rejected(store):
    with pytest.raises(ValueError):
        store.try_acquire_named_lease("live-feed", "owner", 0)
