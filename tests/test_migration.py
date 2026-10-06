"""Migration tooling tests.

The dry-run inventory and checksum logic run against SQLite alone and always
execute. The full apply+verify path needs a PostgreSQL target and is skipped
unless LOKI_TEST_POSTGRES_DSN is set.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

import migrate_sqlite_to_postgres as migrate
from overlay import _system2, planner
from overlay.store import Store

pytestmark = pytest.mark.migration


def _seed(sqlite_path, restart_ticket):
    store = Store(sqlite_path)
    procedures = _system2.load_procedures()
    match = _system2.match_ticket(restart_ticket, procedures)
    run = planner.build_plan(restart_ticket, match)
    store.save_run(run)
    store.append_audit(run.run_id, "run.proposed", "alice")
    store.close()


def test_inventory_counts_and_checksums_are_deterministic(sqlite_path, restart_ticket):
    _seed(sqlite_path, restart_ticket)
    with sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True) as source:
        source.row_factory = sqlite3.Row
        first = migrate.inventory(source)
        second = migrate.inventory(source)

    assert first["runs"]["count"] == 1
    assert first["audit_events"]["count"] == 1
    # A checksum must be stable across repeated reads of the same data.
    assert first["runs"]["sha256"] == second["runs"]["sha256"]


def test_empty_table_checksums_match_the_known_empty_hash(sqlite_path):
    Store(sqlite_path).close()  # create schema, insert nothing
    with sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True) as source:
        source.row_factory = sqlite3.Row
        inventory = migrate.inventory(source)
    empty_hash = migrate.checksum([])
    assert inventory["credentials"]["sha256"] == empty_hash
    assert inventory["credentials"]["count"] == 0


def test_missing_source_database_is_rejected(tmp_path):
    with pytest.raises(RuntimeError):
        migrate._source_connection(tmp_path / "does-not-exist.db")


@pytest.mark.postgres
def test_apply_and_verify_against_postgres(sqlite_path, restart_ticket, monkeypatch):
    dsn = os.getenv("LOKI_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set LOKI_TEST_POSTGRES_DSN to run the full migration")

    _seed(sqlite_path, restart_ticket)
    from overlay.postgres_store import PostgresStore

    target = PostgresStore(dsn, min_size=1, max_size=2, auto_migrate=True)
    with target.connection() as connection:
        for table in ("audit_events", "runs"):
            connection.execute(f"TRUNCATE {table} CASCADE")

    with sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True) as source:
        source.row_factory = sqlite3.Row
        migrate.copy_all(source, target)
        assert migrate.verify(source, target) is True
    target.close()
