"""Explicit, verifiable SQLite -> PostgreSQL migration for BUCKY.

Dry-run is the default and never connects to PostgreSQL. ``--apply`` creates the
versioned PostgreSQL schema, refuses a non-empty target, copies all tables in
one transaction, then verifies row counts and normalized SHA-256 checksums.
No truncate/overwrite option exists: replacing target data must be a separate,
reviewed database operation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from psycopg import sql
from psycopg.types.json import Jsonb

# This script lives in scripts/; the importable packages are one level up at the
# project root. Put the root on sys.path so `overlay` resolves regardless of the
# current working directory (and so the test can import this module).
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overlay.postgres_store import PostgresStore, postgres_connection_info_from_env  # noqa: E402


@dataclass(frozen=True)
class TableSpec:
    name: str
    columns: tuple[str, ...]
    key: str
    json_columns: tuple[str, ...] = ()
    boolean_columns: tuple[str, ...] = ()


TABLES = (
    TableSpec("runs", ("run_id", "ticket_id", "procedure_id", "assignment_group", "state", "created_at", "updated_at", "document"), "run_id", ("document",)),
    TableSpec("approvals", ("approval_id", "run_id", "chunk_id", "step_ids", "binding_digest", "decision", "actor", "reason", "created_at", "consumed_at"), "approval_id", ("step_ids",)),
    TableSpec("audit_events", ("event_id", "run_id", "step_id", "event", "actor", "detail", "created_at"), "event_id", ("detail",)),
    TableSpec("knowledge_sources", ("source_id", "filename", "kind", "file_format", "size_bytes", "sha256", "uploaded_by", "uploaded_at", "status", "document", "content"), "source_id", ("document",)),
    TableSpec("procedure_drafts", ("draft_id", "source_id", "procedure_id", "status", "created_at", "document"), "draft_id", ("document",)),
    TableSpec("published_procedures", ("procedure_id", "name", "source_id", "draft_id", "published_by", "published_at", "enabled", "payload"), "procedure_id", ("payload",), ("enabled",)),
    TableSpec("dictionary_entries", ("entry_id", "source_id", "canonical", "enabled", "created_at", "document"), "entry_id", ("document",), ("enabled",)),
    TableSpec("live_tickets", ("live_id", "ticket_number", "sys_id", "source", "tbl", "status", "fetched_at", "sys_updated_on", "run_id", "document"), "live_id", ("document",)),
    TableSpec("settings", ("key", "value", "updated_at"), "key", ("value",)),
    TableSpec("credentials", ("credential_id", "name", "connector", "account", "secret_ref", "scope", "permissions", "enabled", "created_by", "created_at", "document"), "credential_id", ("scope", "permissions", "document"), ("enabled",)),
    TableSpec("action_executions", ("idempotency_key", "run_id", "chunk_id", "step_id", "approval_id", "action", "action_version", "catalog_version", "connector", "operation", "envelope", "status", "outcome", "error", "created_at", "updated_at"), "idempotency_key", ("envelope",)),
    TableSpec("recommendation_feedback", ("feedback_id", "ticket_ref", "live_id", "procedure_id", "verdict", "created_by", "created_at", "document"), "feedback_id", ("document",)),
    TableSpec("triage_runs", ("triage_id", "live_id", "ticket_ref", "host", "alert_kind", "classification", "routed_to_human", "simulated", "created_by", "created_at", "document"), "triage_id", ("document",), ("routed_to_human", "simulated")),
)


def _source_connection(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise RuntimeError(f"SQLite source does not exist: {path}")
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _source_tables(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {str(row["name"]) for row in rows}


def _source_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _decode(value: Any, is_json: bool, is_boolean: bool) -> Any:
    if value is None:
        return None
    if is_json and isinstance(value, str):
        return json.loads(value)
    if is_boolean:
        return bool(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def source_rows(connection: sqlite3.Connection, spec: TableSpec) -> list[dict[str, Any]]:
    if spec.name not in _source_tables(connection):
        return []
    existing = _source_columns(connection, spec.name)
    selected = [column for column in spec.columns if column in existing]
    rows = connection.execute(
        f'SELECT {", ".join(selected)} FROM "{spec.name}" ORDER BY "{spec.key}"'
    ).fetchall()
    return [
        {
            column: _decode(
                row[column] if column in selected else None,
                column in spec.json_columns,
                column in spec.boolean_columns,
            )
            for column in spec.columns
        }
        for row in rows
    ]


def target_rows(store: PostgresStore, spec: TableSpec) -> list[dict[str, Any]]:
    query = sql.SQL("SELECT {} FROM {} ORDER BY {}").format(
        sql.SQL(", ").join(sql.Identifier(column) for column in spec.columns),
        sql.Identifier(spec.name),
        sql.Identifier(spec.key),
    )
    with store.connection() as connection:
        rows = connection.execute(query).fetchall()
    return [
        {
            column: _decode(row[column], column in spec.json_columns, column in spec.boolean_columns)
            for column in spec.columns
        }
        for row in rows
    ]


def checksum(rows: list[dict[str, Any]]) -> str:
    raw = json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def inventory(source: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    result = {}
    for spec in TABLES:
        rows = source_rows(source, spec)
        result[spec.name] = {"count": len(rows), "sha256": checksum(rows)}
    return result


def copy_all(source: sqlite3.Connection, target: PostgresStore) -> None:
    existing = {spec.name: len(target_rows(target, spec)) for spec in TABLES}
    populated = {name: count for name, count in existing.items() if count}
    if populated:
        raise RuntimeError(
            "PostgreSQL target is not empty; migration refuses to merge or overwrite: "
            + ", ".join(f"{name}={count}" for name, count in populated.items())
        )

    with target.connection() as connection:
        with connection.transaction():
            for spec in TABLES:
                rows = source_rows(source, spec)
                if not rows:
                    continue
                query = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                    sql.Identifier(spec.name),
                    sql.SQL(", ").join(sql.Identifier(c) for c in spec.columns),
                    sql.SQL(", ").join(sql.Placeholder() for _ in spec.columns),
                )
                values = []
                for row in rows:
                    values.append(
                        tuple(
                            Jsonb(row[column]) if column in spec.json_columns else row[column]
                            for column in spec.columns
                        )
                    )
                with connection.cursor() as cursor:
                    cursor.executemany(query, values)


def verify(source: sqlite3.Connection, target: PostgresStore) -> bool:
    okay = True
    print("TABLE                          SOURCE  TARGET  CHECKSUM")
    print("-" * 72)
    for spec in TABLES:
        source_data = source_rows(source, spec)
        target_data = target_rows(target, spec)
        source_hash, target_hash = checksum(source_data), checksum(target_data)
        match = len(source_data) == len(target_data) and source_hash == target_hash
        okay = okay and match
        print(
            f"{spec.name:30} {len(source_data):6}  {len(target_data):6}  "
            f"{'OK' if match else 'MISMATCH'}"
        )
    return okay


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true", help="migrate into an empty PostgreSQL target")
    modes.add_argument("--verify-only", action="store_true", help="compare SQLite with an existing PostgreSQL target")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(os.getenv("OVERLAY_DB", ROOT / "overlay_tasks.db")),
    )
    args = parser.parse_args()

    with _source_connection(args.source) as source:
        if not args.apply and not args.verify_only:
            print(f"DRY RUN - source inventory only: {args.source}")
            print("No PostgreSQL connection was opened and no data was changed.\n")
            for name, details in inventory(source).items():
                print(f"{name:30} {details['count']:6}  {details['sha256'][:16]}")
            print("\nSet PostgreSQL environment variables and use --apply to migrate.")
            return 0

        target = PostgresStore(
            postgres_connection_info_from_env(),
            min_size=1,
            max_size=2,
            auto_migrate=args.apply,
        )
        try:
            if args.apply:
                copy_all(source, target)
            if not verify(source, target):
                raise RuntimeError("post-migration count/checksum verification failed")
            print("\nVerification passed. PostgreSQL target matches the SQLite source.")
            return 0
        finally:
            target.close()


if __name__ == "__main__":
    raise SystemExit(main())
