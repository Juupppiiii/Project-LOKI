"""Persistence behind a repository seam.

SQLite is enough for Phase 1: single host, small team, simulation only. The
seam matters more than the engine -- `Store` is the only module that knows SQL,
so swapping in PostgreSQL for multi-team production means reimplementing this
one class, not touching the domain or the API.

Runs are stored as a JSON document plus indexed columns for queue queries.
Audit events are append-only: there is no update or delete path for them.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .coordination import Lease
from .domain import (
    TERMINAL_RUN_STATES,
    Approval,
    AuditEvent,
    Chunk,
    DictionaryEntry,
    DraftStatus,
    ExecutionPolicy,
    KnowledgeSource,
    LiveTicket,
    LiveTicketStatus,
    Owner,
    ProcedureDraft,
    RiskClass,
    RunState,
    StepRun,
    StepState,
    TaskRun,
    new_id,
    now_iso,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id            TEXT PRIMARY KEY,
    ticket_id         TEXT NOT NULL,
    procedure_id      TEXT NOT NULL,
    assignment_group  TEXT,
    state             TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    document          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_state ON runs(state);
CREATE INDEX IF NOT EXISTS idx_runs_group ON runs(assignment_group);
CREATE INDEX IF NOT EXISTS idx_runs_ticket ON runs(ticket_id);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id     TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    chunk_id        TEXT NOT NULL,
    step_ids        TEXT NOT NULL,
    binding_digest  TEXT NOT NULL,
    decision        TEXT NOT NULL,
    actor           TEXT NOT NULL,
    reason          TEXT,
    created_at      TEXT NOT NULL,
    consumed_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_appr_run ON approvals(run_id);

CREATE TABLE IF NOT EXISTS audit_events (
    event_id    TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL,
    step_id     TEXT,
    event       TEXT NOT NULL,
    actor       TEXT NOT NULL,
    detail      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_run ON audit_events(run_id);

-- Uploaded knowledge -------------------------------------------------------

CREATE TABLE IF NOT EXISTS knowledge_sources (
    source_id    TEXT PRIMARY KEY,
    filename     TEXT NOT NULL,
    kind         TEXT NOT NULL,
    file_format  TEXT NOT NULL,
    size_bytes   INTEGER NOT NULL,
    sha256       TEXT NOT NULL,
    uploaded_by  TEXT NOT NULL,
    uploaded_at  TEXT NOT NULL,
    status       TEXT NOT NULL,
    document     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ks_kind ON knowledge_sources(kind);

CREATE TABLE IF NOT EXISTS procedure_drafts (
    draft_id     TEXT PRIMARY KEY,
    source_id    TEXT NOT NULL,
    procedure_id TEXT NOT NULL,
    status       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    document     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_draft_status ON procedure_drafts(status);
CREATE INDEX IF NOT EXISTS idx_draft_source ON procedure_drafts(source_id);

-- Published procedures: the only upload path the engine ever loads.
CREATE TABLE IF NOT EXISTS published_procedures (
    procedure_id  TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    source_id     TEXT,
    draft_id      TEXT,
    published_by  TEXT NOT NULL,
    published_at  TEXT NOT NULL,
    enabled       INTEGER NOT NULL DEFAULT 1,
    payload       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dictionary_entries (
    entry_id   TEXT PRIMARY KEY,
    source_id  TEXT NOT NULL,
    canonical  TEXT NOT NULL,
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    document   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dict_enabled ON dictionary_entries(enabled);

-- Live ticket intake -------------------------------------------------------

CREATE TABLE IF NOT EXISTS live_tickets (
    live_id        TEXT PRIMARY KEY,
    ticket_number  TEXT NOT NULL,
    sys_id         TEXT NOT NULL,
    source         TEXT NOT NULL,
    tbl            TEXT NOT NULL,
    status         TEXT NOT NULL,
    fetched_at     TEXT NOT NULL,
    sys_updated_on TEXT,
    run_id         TEXT,
    document       TEXT NOT NULL,
    UNIQUE (source, sys_id)
);

CREATE INDEX IF NOT EXISTS idx_live_status ON live_tickets(status);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Access credentials.
--
-- This table holds a POINTER to a secret plus the scope it is allowed to act
-- on. It never holds a secret value: `secret_ref` is something like
-- "env:SN_PASSWORD" or "keyring:overlay/svc-account". Storing passwords in an
-- application database -- especially one without authentication -- would make
-- this app the most attractive target in the estate.
CREATE TABLE IF NOT EXISTS credentials (
    credential_id TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    connector     TEXT NOT NULL,
    account       TEXT NOT NULL,
    secret_ref    TEXT NOT NULL,
    scope         TEXT NOT NULL,
    permissions   TEXT NOT NULL,
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_by    TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    document      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cred_connector ON credentials(connector);

-- Durable execution claim/ledger. The unique idempotency key is acquired before
-- connector dispatch, preventing concurrent requests from executing the same
-- approved step twice. Envelopes contain references and parameters, never secrets.
CREATE TABLE IF NOT EXISTS action_executions (
    idempotency_key TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    chunk_id        TEXT NOT NULL,
    step_id         TEXT NOT NULL,
    approval_id     TEXT NOT NULL,
    action          TEXT NOT NULL,
    action_version  TEXT NOT NULL,
    catalog_version TEXT NOT NULL,
    connector       TEXT NOT NULL,
    operation       TEXT NOT NULL,
    envelope        TEXT NOT NULL,
    status          TEXT NOT NULL,
    outcome         TEXT,
    error           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_exec_run ON action_executions(run_id);
CREATE INDEX IF NOT EXISTS idx_exec_step ON action_executions(step_id);
CREATE INDEX IF NOT EXISTS idx_exec_status ON action_executions(status);

-- Cross-process cache invalidation and singleton work leadership.
CREATE TABLE IF NOT EXISTS cache_revisions (
    name      TEXT PRIMARY KEY,
    revision  INTEGER NOT NULL CHECK (revision >= 0)
);
INSERT OR IGNORE INTO cache_revisions(name, revision)
VALUES ('procedure_registry', 0);

CREATE TRIGGER IF NOT EXISTS published_procedures_revision_insert
AFTER INSERT ON published_procedures BEGIN
    UPDATE cache_revisions SET revision = revision + 1
    WHERE name = 'procedure_registry';
END;
CREATE TRIGGER IF NOT EXISTS published_procedures_revision_update
AFTER UPDATE ON published_procedures BEGIN
    UPDATE cache_revisions SET revision = revision + 1
    WHERE name = 'procedure_registry';
END;
CREATE TRIGGER IF NOT EXISTS published_procedures_revision_delete
AFTER DELETE ON published_procedures BEGIN
    UPDATE cache_revisions SET revision = revision + 1
    WHERE name = 'procedure_registry';
END;
CREATE TRIGGER IF NOT EXISTS dictionary_entries_revision_insert
AFTER INSERT ON dictionary_entries BEGIN
    UPDATE cache_revisions SET revision = revision + 1
    WHERE name = 'procedure_registry';
END;
CREATE TRIGGER IF NOT EXISTS dictionary_entries_revision_update
AFTER UPDATE ON dictionary_entries BEGIN
    UPDATE cache_revisions SET revision = revision + 1
    WHERE name = 'procedure_registry';
END;
CREATE TRIGGER IF NOT EXISTS dictionary_entries_revision_delete
AFTER DELETE ON dictionary_entries BEGIN
    UPDATE cache_revisions SET revision = revision + 1
    WHERE name = 'procedure_registry';
END;

CREATE TABLE IF NOT EXISTS named_leases (
    name          TEXT PRIMARY KEY,
    owner_id      TEXT NOT NULL,
    fencing_token INTEGER NOT NULL CHECK (fencing_token > 0),
    expires_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_named_leases_expiry ON named_leases(expires_at);
"""


def _step_from_dict(d: dict[str, Any]) -> StepRun:
    return StepRun(
        step_id=d["step_id"],
        sequence=d["sequence"],
        action=d["action"],
        description=d["description"],
        rendered=d["rendered"],
        risk=RiskClass(d["risk"]),
        owner=Owner(d["owner"]),
        policy=ExecutionPolicy(d["policy"]),
        chunk_id=d.get("chunk_id"),
        state=StepState(d["state"]),
        outcome=d.get("outcome", ""),
        evidence=d.get("evidence", ""),
        completed_by=d.get("completed_by"),
        completed_at=d.get("completed_at"),
        risk_reason=d.get("risk_reason", ""),
    )


def _chunk_from_dict(d: dict[str, Any]) -> Chunk:
    return Chunk(
        chunk_id=d["chunk_id"],
        label=d["label"],
        step_ids=list(d["step_ids"]),
        risk=RiskClass(d["risk"]),
        owner=Owner(d["owner"]),
        policy=ExecutionPolicy(d["policy"]),
    )


def _run_from_document(doc: dict[str, Any]) -> TaskRun:
    return TaskRun(
        run_id=doc["run_id"],
        ticket_id=doc["ticket_id"],
        ticket=doc["ticket"],
        procedure_id=doc["procedure_id"],
        procedure_name=doc["procedure_name"],
        procedure_version=doc["procedure_version"],
        assignment_group=doc.get("assignment_group", ""),
        state=RunState(doc["state"]),
        steps=[_step_from_dict(s) for s in doc["steps"]],
        chunks=[_chunk_from_dict(c) for c in doc["chunks"]],
        parameters=doc.get("parameters", {}),
        missing_parameters=list(doc.get("missing_parameters", [])),
        match_score=doc.get("match_score", 0.0),
        matched_keywords=list(doc.get("matched_keywords", [])),
        alternatives=doc.get("alternatives", []),
        mode=doc.get("mode", "simulation"),
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
    )


class Store:
    """SQLite implementation of the LOKI repository contract."""

    backend = "sqlite"
    schema_version = 3

    def __init__(self, path: str | Path = "overlay_tasks.db") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # The live poller writes from a background thread while requests write
        # from the server thread. WAL plus one lock keeps that safe on SQLite.
        self._lock = threading.RLock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Additive migrations for databases created by an earlier version.

        Only ADD COLUMN, so an older file keeps working and no data is rewritten.
        """

        cols = {
            r["name"]
            for r in self._conn.execute("PRAGMA table_info(knowledge_sources)")
        }
        if "content" not in cols:
            # The uploaded text, kept so a handbook can be read back in the UI.
            self._conn.execute("ALTER TABLE knowledge_sources ADD COLUMN content TEXT")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def readiness(self) -> dict[str, Any]:
        """Cheap database/schema check for the readiness endpoint."""

        try:
            with self._lock:
                self._conn.execute("SELECT 1").fetchone()
                required = {"action_executions", "cache_revisions", "named_leases"}
                rows = self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name IN ('action_executions','cache_revisions','named_leases')"
                ).fetchall()
            found = {row["name"] for row in rows}
            missing = sorted(required - found)
            if missing:
                raise RuntimeError("required schema tables are missing: " + ", ".join(missing))
            return {
                "ready": True,
                "backend": self.backend,
                "schema_version": self.schema_version,
            }
        except Exception as exc:
            return {
                "ready": False,
                "backend": self.backend,
                "schema_version": self.schema_version,
                "detail": str(exc)[:200],
            }

    # -- coordination ----------------------------------------------------

    def get_registry_revision(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT revision FROM cache_revisions WHERE name = ?",
                ("procedure_registry",),
            ).fetchone()
        return int(row["revision"]) if row else 0

    @staticmethod
    def _lease_from_row(row: sqlite3.Row) -> Lease:
        expires = datetime.fromtimestamp(int(row["expires_at"]), timezone.utc).isoformat()
        return Lease(
            name=row["name"],
            owner_id=row["owner_id"],
            fencing_token=int(row["fencing_token"]),
            expires_at=expires,
        )

    def try_acquire_named_lease(
        self, name: str, owner_id: str, ttl_seconds: int
    ) -> Lease | None:
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be positive")
        now_sql = "CAST(strftime('%s','now') AS INTEGER)"
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    f"""
                    INSERT INTO named_leases(name, owner_id, fencing_token, expires_at)
                    VALUES (?, ?, 1, {now_sql} + ?)
                    ON CONFLICT(name) DO UPDATE SET
                        owner_id = excluded.owner_id,
                        fencing_token = CASE
                            WHEN named_leases.expires_at <= {now_sql}
                            THEN named_leases.fencing_token + 1
                            ELSE named_leases.fencing_token
                        END,
                        expires_at = {now_sql} + ?
                    WHERE named_leases.expires_at <= {now_sql}
                       OR named_leases.owner_id = excluded.owner_id
                    RETURNING name, owner_id, fencing_token, expires_at
                    """,
                    (name, owner_id, ttl_seconds, ttl_seconds),
                ).fetchone()
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return self._lease_from_row(row) if row else None

    def renew_named_lease(
        self, name: str, owner_id: str, fencing_token: int, ttl_seconds: int
    ) -> Lease | None:
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be positive")
        now_sql = "CAST(strftime('%s','now') AS INTEGER)"
        with self._lock:
            row = self._conn.execute(
                f"""UPDATE named_leases SET expires_at = {now_sql} + ?
                WHERE name = ? AND owner_id = ? AND fencing_token = ?
                  AND expires_at > {now_sql}
                RETURNING name, owner_id, fencing_token, expires_at""",
                (ttl_seconds, name, owner_id, fencing_token),
            ).fetchone()
            self._conn.commit()
        return self._lease_from_row(row) if row else None

    def release_named_lease(
        self, name: str, owner_id: str, fencing_token: int
    ) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                """UPDATE named_leases SET expires_at = 0
                WHERE name = ? AND owner_id = ? AND fencing_token = ?""",
                (name, owner_id, fencing_token),
            )
            self._conn.commit()
        return cursor.rowcount == 1

    def get_named_lease(self, name: str) -> Lease | None:
        now_sql = "CAST(strftime('%s','now') AS INTEGER)"
        with self._lock:
            row = self._conn.execute(
                f"""SELECT name, owner_id, fencing_token, expires_at
                FROM named_leases WHERE name = ? AND expires_at > {now_sql}""",
                (name,),
            ).fetchone()
        return self._lease_from_row(row) if row else None

    # -- runs ------------------------------------------------------------

    def save_run(self, run: TaskRun) -> None:
        run.updated_at = now_iso()
        doc = json.dumps(run.to_dict(), default=str)
        with self._lock:
            self._save_run_locked(run, doc)

    def _save_run_locked(self, run: TaskRun, doc: str) -> None:
        self._conn.execute(
            """
            INSERT INTO runs (run_id, ticket_id, procedure_id, assignment_group,
                              state, created_at, updated_at, document)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                state = excluded.state,
                updated_at = excluded.updated_at,
                document = excluded.document
            """,
            (
                run.run_id,
                run.ticket_id,
                run.procedure_id,
                run.assignment_group,
                run.state.value,
                run.created_at,
                run.updated_at,
                doc,
            ),
        )
        self._conn.commit()

    def get_run(self, run_id: str) -> TaskRun | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT document FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return _run_from_document(json.loads(row["document"]))

    def list_runs(
        self,
        state: str | None = None,
        assignment_group: str | None = None,
        limit: int = 100,
    ) -> list[TaskRun]:
        sql = "SELECT document FROM runs"
        clauses: list[str] = []
        args: list[Any] = []

        if state:
            clauses.append("state = ?")
            args.append(state)
        if assignment_group:
            clauses.append("assignment_group = ?")
            args.append(assignment_group)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)

        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)

        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [_run_from_document(json.loads(r["document"])) for r in rows]

    def find_run_by_ticket(self, ticket_id: str) -> TaskRun | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT document FROM runs WHERE ticket_id = ? ORDER BY created_at DESC LIMIT 1",
                (ticket_id,),
            ).fetchone()
        if row is None:
            return None
        return _run_from_document(json.loads(row["document"]))

    def find_active_run_by_ticket(self, ticket_id: str) -> TaskRun | None:
        """The most recent run for a ticket that has not finished.

        Terminal runs are ignored: once a ticket is completed, failed or
        cancelled, starting again is normal and needs no confirmation.
        """

        terminal = [s.value for s in TERMINAL_RUN_STATES]
        placeholders = ",".join("?" for _ in terminal)
        with self._lock:
            row = self._conn.execute(
                f"""
                SELECT document FROM runs
                WHERE ticket_id = ? AND state NOT IN ({placeholders})
                ORDER BY created_at DESC LIMIT 1
                """,
                (ticket_id, *terminal),
            ).fetchone()
        if row is None:
            return None
        return _run_from_document(json.loads(row["document"]))

    def count_runs_by_ticket(self, ticket_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM runs WHERE ticket_id = ?", (ticket_id,)
            ).fetchone()
        return int(row["n"]) if row else 0

    # -- approvals -------------------------------------------------------

    def save_approval(self, approval: Approval) -> None:
        with self._lock:
            self._save_approval_locked(approval)

    def _save_approval_locked(self, approval: Approval) -> None:
        self._conn.execute(
            """
            INSERT INTO approvals (approval_id, run_id, chunk_id, step_ids,
                                   binding_digest, decision, actor, reason,
                                   created_at, consumed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(approval_id) DO UPDATE SET
                consumed_at = excluded.consumed_at
            """,
            (
                approval.approval_id,
                approval.run_id,
                approval.chunk_id,
                json.dumps(approval.step_ids),
                approval.binding_digest,
                approval.decision,
                approval.actor,
                approval.reason,
                approval.created_at,
                approval.consumed_at,
            ),
        )
        self._conn.commit()

    def supersede_approvals(self, run_id: str, chunk_id: str, at: str) -> int:
        """Retire any outstanding approvals for a chunk.

        Called before recording a new decision so exactly one approval can ever
        be active. Without this, two decisions made in the same second are
        ambiguous -- timestamps are second-resolution -- and a stale approval
        could win the ordering and authorise the wrong plan.
        """

        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE approvals SET consumed_at = ?
                WHERE run_id = ? AND chunk_id = ? AND consumed_at IS NULL
                """,
                (at, run_id, chunk_id),
            )
            self._conn.commit()
            return cur.rowcount

    def active_approval(self, run_id: str, chunk_id: str) -> Approval | None:
        """The one unconsumed approval for a chunk, if any.

        `rowid DESC` is a deliberate tiebreaker: `created_at` only has
        second resolution, so it cannot order two decisions made in the same
        second on its own.
        """

        row = self._conn.execute(
            """
            SELECT * FROM approvals
            WHERE run_id = ? AND chunk_id = ? AND decision = 'approved'
              AND consumed_at IS NULL
            ORDER BY created_at DESC, rowid DESC LIMIT 1
            """,
            (run_id, chunk_id),
        ).fetchone()
        if row is None:
            return None
        return Approval(
            approval_id=row["approval_id"],
            run_id=row["run_id"],
            chunk_id=row["chunk_id"],
            step_ids=json.loads(row["step_ids"]),
            binding_digest=row["binding_digest"],
            decision=row["decision"],
            actor=row["actor"],
            reason=row["reason"] or "",
            created_at=row["created_at"],
            consumed_at=row["consumed_at"],
        )

    def list_approvals(self, run_id: str) -> list[Approval]:
        rows = self._conn.execute(
            "SELECT * FROM approvals WHERE run_id = ? ORDER BY created_at",
            (run_id,),
        ).fetchall()
        return [
            Approval(
                approval_id=r["approval_id"],
                run_id=r["run_id"],
                chunk_id=r["chunk_id"],
                step_ids=json.loads(r["step_ids"]),
                binding_digest=r["binding_digest"],
                decision=r["decision"],
                actor=r["actor"],
                reason=r["reason"] or "",
                created_at=r["created_at"],
                consumed_at=r["consumed_at"],
            )
            for r in rows
        ]

    # -- audit (append-only) --------------------------------------------

    def append_audit(
        self,
        run_id: str,
        event: str,
        actor: str,
        detail: dict[str, Any] | None = None,
        step_id: str | None = None,
    ) -> AuditEvent:
        ev = AuditEvent(
            event_id=new_id("evt"),
            run_id=run_id,
            event=event,
            actor=actor,
            detail=detail or {},
            step_id=step_id,
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO audit_events (event_id, run_id, step_id, event, actor,
                                          detail, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ev.event_id,
                    ev.run_id,
                    ev.step_id,
                    ev.event,
                    ev.actor,
                    json.dumps(ev.detail, default=str),
                    ev.created_at,
                ),
            )
            self._conn.commit()
        return ev

    def list_audit(self, run_id: str) -> list[AuditEvent]:
        rows = self._conn.execute(
            "SELECT * FROM audit_events WHERE run_id = ? ORDER BY created_at, rowid",
            (run_id,),
        ).fetchall()
        return [
            AuditEvent(
                event_id=r["event_id"],
                run_id=r["run_id"],
                event=r["event"],
                actor=r["actor"],
                detail=json.loads(r["detail"]),
                step_id=r["step_id"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # -- execution ledger ------------------------------------------------

    def claim_action_execution(
        self, envelope: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        """Atomically claim an idempotency key before connector dispatch.

        Returns ``(True, record)`` only to the caller that inserted the key.
        Every concurrent/replayed caller receives the existing record and must
        not dispatch the connector again.
        """

        key = str(envelope["idempotency_key"])
        now = now_iso()
        payload = (
            key,
            envelope["run_id"],
            envelope["chunk_id"],
            envelope["step_id"],
            envelope["approval_id"],
            envelope["action"],
            envelope["action_version"],
            envelope["catalog_version"],
            envelope["connector"],
            envelope["operation"],
            json.dumps(envelope, sort_keys=True, default=str),
            now,
            now,
        )
        # SQLite serialises writers, so a concurrent claimant can transiently
        # see "database is locked". Retry briefly so a loser resolves to a clean
        # (False, existing) rather than raising; the unique key still guarantees
        # exactly one winner. PostgreSQL handles this natively.
        deadline = time.monotonic() + 5.0
        while True:
            with self._lock:
                try:
                    self._conn.execute(
                        """
                        INSERT INTO action_executions (
                            idempotency_key, run_id, chunk_id, step_id, approval_id,
                            action, action_version, catalog_version, connector,
                            operation, envelope, status, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'claimed', ?, ?)
                        """,
                        payload,
                    )
                    self._conn.commit()
                    return True, self._action_execution_locked(key)
                except sqlite3.IntegrityError:
                    return False, self._action_execution_locked(key)
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).lower():
                        raise
                    # Another connection holds the write lock. If it has already
                    # committed the claim, we cleanly lost; otherwise back off.
                    existing = self._conn.execute(
                        "SELECT 1 FROM action_executions WHERE idempotency_key = ?",
                        (key,),
                    ).fetchone()
                    if existing is not None:
                        return False, self._action_execution_locked(key)
                    if time.monotonic() > deadline:
                        raise
            time.sleep(0.05)

    def _action_execution_locked(self, key: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT * FROM action_executions WHERE idempotency_key = ?", (key,)
        ).fetchone()
        if row is None:
            raise KeyError(key)
        record = dict(row)
        record["envelope"] = json.loads(record["envelope"])
        return record

    def get_action_execution(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM action_executions WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if row is None:
                return None
            record = dict(row)
            record["envelope"] = json.loads(record["envelope"])
            return record

    def complete_action_execution(self, key: str, outcome: str) -> None:
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE action_executions
                SET status = 'succeeded', outcome = ?, error = NULL, updated_at = ?
                WHERE idempotency_key = ? AND status = 'claimed'
                """,
                (outcome, now_iso(), key),
            )
            if cur.rowcount != 1:
                self._conn.rollback()
                raise RuntimeError(f"execution '{key}' is not in claimed state")
            self._conn.commit()

    def fail_action_execution(self, key: str, error: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE action_executions
                SET status = 'failed', error = ?, updated_at = ?
                WHERE idempotency_key = ? AND status = 'claimed'
                """,
                (error[:1000], now_iso(), key),
            )
            self._conn.commit()

    # -- queue stats -----------------------------------------------------

    def counts_by_state(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT state, COUNT(*) AS n FROM runs GROUP BY state"
        ).fetchall()
        return {r["state"]: r["n"] for r in rows}

    # -- knowledge sources ----------------------------------------------

    def save_source(self, src: KnowledgeSource, content: str | None = None) -> None:
        """Persist an uploaded source.

        `content` is the decoded file text, stored so the handbook can be read
        back in the UI. It is kept in its own column rather than in the metadata
        document so listing sources stays cheap.
        """

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO knowledge_sources (source_id, filename, kind,
                    file_format, size_bytes, sha256, uploaded_by, uploaded_at,
                    status, document, content)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    status = excluded.status,
                    document = excluded.document,
                    content = COALESCE(excluded.content, knowledge_sources.content)
                """,
                (
                    src.source_id,
                    src.filename,
                    src.kind.value,
                    src.file_format,
                    src.size_bytes,
                    src.sha256,
                    src.uploaded_by,
                    src.uploaded_at,
                    src.status,
                    json.dumps(src.to_dict(), default=str),
                    content,
                ),
            )
            self._conn.commit()

    def get_source_content(self, source_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT content FROM knowledge_sources WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        return row["content"] if row else None

    def list_sources(self, kind: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT document FROM knowledge_sources"
        args: list[Any] = []
        if kind:
            sql += " WHERE kind = ?"
            args.append(kind)
        sql += " ORDER BY uploaded_at DESC"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [json.loads(r["document"]) for r in rows]

    def get_source(self, source_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT document FROM knowledge_sources WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        return json.loads(row["document"]) if row else None

    def find_source_by_hash(self, sha256: str) -> dict[str, Any] | None:
        """Used to warn on re-uploading an identical file."""

        with self._lock:
            row = self._conn.execute(
                "SELECT document FROM knowledge_sources WHERE sha256 = ? LIMIT 1",
                (sha256,),
            ).fetchone()
        return json.loads(row["document"]) if row else None

    # -- procedure drafts ------------------------------------------------

    def save_draft(self, draft: ProcedureDraft) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO procedure_drafts (draft_id, source_id, procedure_id,
                                              status, created_at, document)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(draft_id) DO UPDATE SET
                    status = excluded.status,
                    document = excluded.document
                """,
                (
                    draft.draft_id,
                    draft.source_id,
                    draft.procedure_id,
                    draft.status.value,
                    draft.created_at,
                    json.dumps(draft.to_dict(), default=str),
                ),
            )
            self._conn.commit()

    def _draft_from_doc(self, doc: dict[str, Any]) -> ProcedureDraft:
        return ProcedureDraft(
            draft_id=doc["draft_id"],
            source_id=doc["source_id"],
            procedure_id=doc["procedure_id"],
            name=doc["name"],
            payload=doc["payload"],
            status=DraftStatus(doc["status"]),
            validation_errors=list(doc.get("validation_errors", [])),
            risk_warnings=list(doc.get("risk_warnings", [])),
            reviewed_by=doc.get("reviewed_by"),
            reviewed_at=doc.get("reviewed_at"),
            review_note=doc.get("review_note", ""),
            created_at=doc["created_at"],
        )

    def get_draft(self, draft_id: str) -> ProcedureDraft | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT document FROM procedure_drafts WHERE draft_id = ?",
                (draft_id,),
            ).fetchone()
        return self._draft_from_doc(json.loads(row["document"])) if row else None

    def list_drafts(
        self, status: str | None = None, source_id: str | None = None
    ) -> list[ProcedureDraft]:
        sql = "SELECT document FROM procedure_drafts"
        clauses: list[str] = []
        args: list[Any] = []
        if status:
            clauses.append("status = ?")
            args.append(status)
        if source_id:
            clauses.append("source_id = ?")
            args.append(source_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [self._draft_from_doc(json.loads(r["document"])) for r in rows]

    # -- published procedures --------------------------------------------

    def publish_procedure(
        self,
        procedure_id: str,
        name: str,
        payload: dict[str, Any],
        published_by: str,
        source_id: str | None = None,
        draft_id: str | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO published_procedures (procedure_id, name, source_id,
                    draft_id, published_by, published_at, enabled, payload)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(procedure_id) DO UPDATE SET
                    name = excluded.name,
                    source_id = excluded.source_id,
                    draft_id = excluded.draft_id,
                    published_by = excluded.published_by,
                    published_at = excluded.published_at,
                    enabled = 1,
                    payload = excluded.payload
                """,
                (
                    procedure_id,
                    name,
                    source_id,
                    draft_id,
                    published_by,
                    now_iso(),
                    json.dumps(payload, default=str),
                ),
            )
            self._conn.commit()

    def list_published(self, only_enabled: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM published_procedures"
        if only_enabled:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY published_at DESC"
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        return [
            {
                "procedure_id": r["procedure_id"],
                "name": r["name"],
                "source_id": r["source_id"],
                "draft_id": r["draft_id"],
                "published_by": r["published_by"],
                "published_at": r["published_at"],
                "enabled": bool(r["enabled"]),
                "payload": json.loads(r["payload"]),
            }
            for r in rows
        ]

    def set_published_enabled(self, procedure_id: str, enabled: bool) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE published_procedures SET enabled = ? WHERE procedure_id = ?",
                (1 if enabled else 0, procedure_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    # -- dictionary ------------------------------------------------------

    def save_dictionary_entry(self, entry: DictionaryEntry) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO dictionary_entries (entry_id, source_id, canonical,
                                                enabled, created_at, document)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(entry_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    document = excluded.document
                """,
                (
                    entry.entry_id,
                    entry.source_id,
                    entry.canonical,
                    1 if entry.enabled else 0,
                    entry.created_at,
                    json.dumps(entry.to_dict(), default=str),
                ),
            )
            self._conn.commit()

    def list_dictionary(self, only_enabled: bool = True) -> list[DictionaryEntry]:
        sql = "SELECT document FROM dictionary_entries"
        if only_enabled:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY canonical"
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        out = []
        for r in rows:
            d = json.loads(r["document"])
            out.append(
                DictionaryEntry(
                    entry_id=d["entry_id"],
                    source_id=d["source_id"],
                    canonical=d["canonical"],
                    aliases=list(d.get("aliases", [])),
                    note=d.get("note", ""),
                    enabled=bool(d.get("enabled", True)),
                    created_at=d["created_at"],
                )
            )
        return out

    def set_dictionary_enabled(self, entry_id: str, enabled: bool) -> bool:
        entry = None
        with self._lock:
            row = self._conn.execute(
                "SELECT document FROM dictionary_entries WHERE entry_id = ?",
                (entry_id,),
            ).fetchone()
            if row is None:
                return False
            entry = json.loads(row["document"])
            entry["enabled"] = enabled
            self._conn.execute(
                "UPDATE dictionary_entries SET enabled = ?, document = ? WHERE entry_id = ?",
                (1 if enabled else 0, json.dumps(entry), entry_id),
            )
            self._conn.commit()
        return True

    def delete_dictionary_by_source(self, source_id: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM dictionary_entries WHERE source_id = ?", (source_id,)
            )
            self._conn.commit()
            return cur.rowcount

    # -- live tickets ----------------------------------------------------

    def upsert_live_ticket(self, t: LiveTicket) -> bool:
        """Insert a live ticket. Returns True if it was new.

        Keyed on (source, sys_id) so re-polling the same ticket does not create
        duplicates or reset a triage decision a human already made.
        """

        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO live_tickets (live_id, ticket_number, sys_id, source,
                    tbl, status, fetched_at, sys_updated_on, run_id, document)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, sys_id) DO NOTHING
                """,
                (
                    t.live_id,
                    t.ticket_number,
                    t.sys_id,
                    t.source,
                    t.table,
                    t.status.value,
                    t.fetched_at,
                    t.sys_updated_on,
                    t.run_id,
                    json.dumps(t.to_dict(), default=str),
                ),
            )
            self._conn.commit()
            return cursor.rowcount == 1

    def _live_from_doc(self, doc: dict[str, Any]) -> LiveTicket:
        return LiveTicket(
            live_id=doc["live_id"],
            ticket_number=doc["ticket_number"],
            sys_id=doc["sys_id"],
            source=doc["source"],
            table=doc["table"],
            payload=doc["payload"],
            status=LiveTicketStatus(doc["status"]),
            match=doc.get("match", {}),
            run_id=doc.get("run_id"),
            fetched_at=doc["fetched_at"],
            sys_updated_on=doc.get("sys_updated_on", ""),
        )

    def get_live_ticket(self, live_id: str) -> LiveTicket | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT document FROM live_tickets WHERE live_id = ?", (live_id,)
            ).fetchone()
        return self._live_from_doc(json.loads(row["document"])) if row else None

    def update_live_ticket(self, t: LiveTicket) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE live_tickets SET status = ?, run_id = ?, document = ?
                WHERE live_id = ?
                """,
                (t.status.value, t.run_id, json.dumps(t.to_dict(), default=str), t.live_id),
            )
            self._conn.commit()

    def list_live_tickets(
        self, status: str | None = None, limit: int = 100
    ) -> list[LiveTicket]:
        sql = "SELECT document FROM live_tickets"
        args: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            args.append(status)
        sql += " ORDER BY fetched_at DESC, rowid DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [self._live_from_doc(json.loads(r["document"])) for r in rows]

    def live_counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM live_tickets GROUP BY status"
            ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    def known_sys_ids(self, source: str) -> set[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT sys_id FROM live_tickets WHERE source = ?", (source,)
            ).fetchall()
        return {r["sys_id"] for r in rows}

    # -- settings --------------------------------------------------------

    def set_setting(self, key: str, value: Any) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, json.dumps(value, default=str), now_iso()),
            )
            self._conn.commit()

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return json.loads(row["value"]) if row else default

    def delete_source_cascade(self, source_id: str) -> dict[str, int]:
        """Delete an upload and everything derived from it.

        Published procedures are removed too. Leaving them active with their
        origin deleted would make the rule set unexplainable, which is worse
        than withdrawing them.
        """

        with self._lock:
            pubs = self._conn.execute(
                "DELETE FROM published_procedures WHERE source_id = ?", (source_id,)
            ).rowcount
            drafts = self._conn.execute(
                "DELETE FROM procedure_drafts WHERE source_id = ?", (source_id,)
            ).rowcount
            entries = self._conn.execute(
                "DELETE FROM dictionary_entries WHERE source_id = ?", (source_id,)
            ).rowcount
            src = self._conn.execute(
                "DELETE FROM knowledge_sources WHERE source_id = ?", (source_id,)
            ).rowcount
            self._conn.commit()

        return {
            "sources": src,
            "drafts": drafts,
            "published_procedures": pubs,
            "dictionary_entries": entries,
        }

    # -- credentials -----------------------------------------------------
    # Only references and scope are stored here. A secret value never enters
    # this database; see overlay/credentials.py for where values actually live.

    def save_credential(self, cred: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO credentials (credential_id, name, connector, account,
                    secret_ref, scope, permissions, enabled, created_by,
                    created_at, document)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(credential_id) DO UPDATE SET
                    name = excluded.name,
                    connector = excluded.connector,
                    account = excluded.account,
                    secret_ref = excluded.secret_ref,
                    scope = excluded.scope,
                    permissions = excluded.permissions,
                    enabled = excluded.enabled,
                    document = excluded.document
                """,
                (
                    cred["credential_id"],
                    cred["name"],
                    cred["connector"],
                    cred["account"],
                    cred["secret_ref"],
                    json.dumps(cred.get("scope", [])),
                    json.dumps(cred.get("permissions", [])),
                    1 if cred.get("enabled", True) else 0,
                    cred["created_by"],
                    cred["created_at"],
                    json.dumps(cred, default=str),
                ),
            )
            self._conn.commit()

    def list_credentials(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT document FROM credentials ORDER BY created_at DESC"
            ).fetchall()
        return [json.loads(r["document"]) for r in rows]

    def get_credential(self, credential_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT document FROM credentials WHERE credential_id = ?",
                (credential_id,),
            ).fetchone()
        return json.loads(row["document"]) if row else None

    def delete_credential(self, credential_id: str) -> bool:
        with self._lock:
            n = self._conn.execute(
                "DELETE FROM credentials WHERE credential_id = ?", (credential_id,)
            ).rowcount
            self._conn.commit()
        return n > 0
