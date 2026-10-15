"""PostgreSQL implementation of the LOKI repository contract.

The adapter preserves the existing domain and API shapes while using JSONB,
explicit ordering columns, conflict-safe inserts, and pooled connections. It
never falls back to SQLite. Production migrations are expected to run as a
one-off task; automatic migration is available only through the explicit
LOKI_POSTGRES_AUTO_MIGRATE flag or migration tool.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from psycopg import conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .coordination import Lease
from .domain import (
    TERMINAL_RUN_STATES,
    Approval,
    AuditEvent,
    DictionaryEntry,
    DraftStatus,
    KnowledgeSource,
    LiveTicket,
    LiveTicketStatus,
    ProcedureDraft,
    TaskRun,
    new_id,
    now_iso,
)
from .store import _run_from_document

REQUIRED_SCHEMA_VERSION = 2
_MIGRATIONS = Path(__file__).resolve().parent / "sql" / "postgres"


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _approval(row: dict[str, Any]) -> Approval:
    return Approval(
        approval_id=row["approval_id"],
        run_id=row["run_id"],
        chunk_id=row["chunk_id"],
        step_ids=list(row["step_ids"]),
        binding_digest=row["binding_digest"],
        decision=row["decision"],
        actor=row["actor"],
        reason=row.get("reason") or "",
        created_at=_iso(row["created_at"]),
        consumed_at=_iso(row.get("consumed_at")),
    )


def _execution(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for key in ("created_at", "updated_at", "lease_expires_at"):
        out[key] = _iso(out.get(key))
    return out


def postgres_connection_info_from_env() -> str:
    """Build connection information without logging or returning secret fields."""

    dsn = os.getenv("LOKI_POSTGRES_DSN", "").strip()
    if dsn:
        return dsn
    required = {
        "host": os.getenv("LOKI_POSTGRES_HOST"),
        "dbname": os.getenv("LOKI_POSTGRES_DB", "loki"),
        "user": os.getenv("LOKI_POSTGRES_USER"),
        "password": os.getenv("LOKI_POSTGRES_PASSWORD"),
    }
    missing = [f"LOKI_POSTGRES_{k.upper()}" for k, value in required.items() if not value]
    if missing:
        raise RuntimeError(
            "PostgreSQL connection settings are incomplete; missing " + ", ".join(missing)
        )
    return conninfo.make_conninfo(
        host=required["host"],
        port=os.getenv("LOKI_POSTGRES_PORT", "5432"),
        dbname=required["dbname"],
        user=required["user"],
        password=required["password"],
        sslmode=os.getenv("LOKI_POSTGRES_SSLMODE", "require"),
        connect_timeout="5",
    )


class PostgresStore:
    backend = "postgres"
    schema_version = REQUIRED_SCHEMA_VERSION

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 10,
        auto_migrate: bool = False,
    ) -> None:
        if not dsn.strip():
            raise RuntimeError("PostgreSQL connection information is empty")
        self._pool = ConnectionPool(
            dsn,
            min_size=min_size,
            max_size=max_size,
            kwargs={"row_factory": dict_row},
            open=True,
        )
        self._pool.wait(timeout=10)
        if auto_migrate:
            self.migrate()

    @classmethod
    def from_env(cls) -> "PostgresStore":
        store = cls(
            postgres_connection_info_from_env(),
            min_size=int(os.getenv("LOKI_POSTGRES_POOL_MIN", "1")),
            max_size=int(os.getenv("LOKI_POSTGRES_POOL_MAX", "10")),
            auto_migrate=os.getenv("LOKI_POSTGRES_AUTO_MIGRATE", "0").lower()
            in ("1", "true", "yes"),
        )
        status = store.readiness()
        if not status["ready"]:
            store.close()
            raise RuntimeError(
                "PostgreSQL is reachable but not ready: " + status.get("detail", "unknown")
            )
        return store

    def close(self) -> None:
        self._pool.close()

    def connection(self):
        """Migration/administration connection context; application code uses methods."""

        return self._pool.connection()

    def migrate(self) -> list[int]:
        """Apply pending versioned migrations under a PostgreSQL advisory lock."""

        applied: list[int] = []
        with self._pool.connection() as connection:
            with connection.transaction():
                connection.execute("SELECT pg_advisory_xact_lock(%s)", (0x4C4F4B49,))
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY, name TEXT NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)"""
                )
                rows = connection.execute("SELECT version FROM schema_migrations").fetchall()
                existing = {int(row["version"]) for row in rows}
                for path in sorted(_MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")):
                    version = int(path.name.split("_", 1)[0])
                    if version in existing:
                        continue
                    connection.execute(path.read_text(encoding="utf-8"))
                    connection.execute(
                        "INSERT INTO schema_migrations (version, name) VALUES (%s, %s)",
                        (version, path.name),
                    )
                    applied.append(version)
        return applied

    def readiness(self) -> dict[str, Any]:
        try:
            with self._pool.connection() as connection:
                connection.execute("SELECT 1").fetchone()
                row = connection.execute(
                    "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
                ).fetchone()
                version = int(row["version"])
            if version < REQUIRED_SCHEMA_VERSION:
                raise RuntimeError(
                    f"schema version {version}; required {REQUIRED_SCHEMA_VERSION}"
                )
            return {"ready": True, "backend": self.backend, "schema_version": version}
        except Exception as exc:
            return {
                "ready": False,
                "backend": self.backend,
                "schema_version": 0,
                "detail": str(exc)[:200],
            }

    # Coordination --------------------------------------------------------

    def get_registry_revision(self) -> int:
        with self._pool.connection() as connection:
            row = connection.execute(
                "SELECT revision FROM cache_revisions WHERE name=%s",
                ("procedure_registry",),
            ).fetchone()
        return int(row["revision"]) if row else 0

    @staticmethod
    def _lease(row: dict[str, Any]) -> Lease:
        return Lease(
            name=row["name"],
            owner_id=row["owner_id"],
            fencing_token=int(row["fencing_token"]),
            expires_at=_iso(row["expires_at"]),
        )

    def try_acquire_named_lease(
        self, name: str, owner_id: str, ttl_seconds: int
    ) -> Lease | None:
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be positive")
        with self._pool.connection() as connection:
            row = connection.execute(
                """INSERT INTO named_leases(name,owner_id,fencing_token,expires_at)
                VALUES (%s,%s,1,statement_timestamp() + (%s * interval '1 second'))
                ON CONFLICT(name) DO UPDATE SET
                    owner_id=EXCLUDED.owner_id,
                    fencing_token=CASE
                        WHEN named_leases.expires_at <= statement_timestamp()
                        THEN named_leases.fencing_token + 1
                        ELSE named_leases.fencing_token
                    END,
                    expires_at=statement_timestamp() + (%s * interval '1 second')
                WHERE named_leases.expires_at <= statement_timestamp()
                   OR named_leases.owner_id=EXCLUDED.owner_id
                RETURNING name,owner_id,fencing_token,expires_at""",
                (name, owner_id, ttl_seconds, ttl_seconds),
            ).fetchone()
        return self._lease(row) if row else None

    def renew_named_lease(
        self, name: str, owner_id: str, fencing_token: int, ttl_seconds: int
    ) -> Lease | None:
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be positive")
        with self._pool.connection() as connection:
            row = connection.execute(
                """UPDATE named_leases
                SET expires_at=statement_timestamp() + (%s * interval '1 second')
                WHERE name=%s AND owner_id=%s AND fencing_token=%s
                  AND expires_at > statement_timestamp()
                RETURNING name,owner_id,fencing_token,expires_at""",
                (ttl_seconds, name, owner_id, fencing_token),
            ).fetchone()
        return self._lease(row) if row else None

    def release_named_lease(
        self, name: str, owner_id: str, fencing_token: int
    ) -> bool:
        with self._pool.connection() as connection:
            cursor = connection.execute(
                """UPDATE named_leases SET expires_at=to_timestamp(0)
                WHERE name=%s AND owner_id=%s AND fencing_token=%s""",
                (name, owner_id, fencing_token),
            )
            return cursor.rowcount == 1

    def get_named_lease(self, name: str) -> Lease | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                """SELECT name,owner_id,fencing_token,expires_at
                FROM named_leases WHERE name=%s
                  AND expires_at > statement_timestamp()""",
                (name,),
            ).fetchone()
        return self._lease(row) if row else None

    # Runs -----------------------------------------------------------------

    def save_run(self, run: TaskRun) -> None:
        run.updated_at = now_iso()
        with self._pool.connection() as connection:
            connection.execute(
                """INSERT INTO runs
                (run_id,ticket_id,procedure_id,assignment_group,state,created_at,updated_at,document)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(run_id) DO UPDATE SET state=EXCLUDED.state,
                updated_at=EXCLUDED.updated_at, document=EXCLUDED.document""",
                (
                    run.run_id, run.ticket_id, run.procedure_id, run.assignment_group,
                    run.state.value, run.created_at, run.updated_at, Jsonb(run.to_dict()),
                ),
            )

    def get_run(self, run_id: str) -> TaskRun | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                "SELECT document FROM runs WHERE run_id=%s", (run_id,)
            ).fetchone()
        return _run_from_document(row["document"]) if row else None

    def list_runs(self, state: str | None = None, assignment_group: str | None = None, limit: int = 100) -> list[TaskRun]:
        clauses, args = [], []
        if state:
            clauses.append("state=%s")
            args.append(state)
        if assignment_group:
            clauses.append("assignment_group=%s")
            args.append(assignment_group)
        query = "SELECT document FROM runs"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT %s"
        args.append(limit)
        with self._pool.connection() as connection:
            rows = connection.execute(query, args).fetchall()
        return [_run_from_document(row["document"]) for row in rows]

    def find_run_by_ticket(self, ticket_id: str) -> TaskRun | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                "SELECT document FROM runs WHERE ticket_id=%s ORDER BY created_at DESC LIMIT 1",
                (ticket_id,),
            ).fetchone()
        return _run_from_document(row["document"]) if row else None

    def find_active_run_by_ticket(self, ticket_id: str) -> TaskRun | None:
        terminal = [state.value for state in TERMINAL_RUN_STATES]
        placeholders = ",".join("%s" for _ in terminal)
        query = f"SELECT document FROM runs WHERE ticket_id=%s AND state NOT IN ({placeholders}) ORDER BY created_at DESC LIMIT 1"
        with self._pool.connection() as connection:
            row = connection.execute(query, (ticket_id, *terminal)).fetchone()
        return _run_from_document(row["document"]) if row else None

    def count_runs_by_ticket(self, ticket_id: str) -> int:
        with self._pool.connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS n FROM runs WHERE ticket_id=%s", (ticket_id,)
            ).fetchone()
        return int(row["n"])

    # Approvals and audit --------------------------------------------------

    def save_approval(self, approval: Approval) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                """INSERT INTO approvals
                (approval_id,run_id,chunk_id,step_ids,binding_digest,decision,actor,reason,created_at,consumed_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(approval_id) DO UPDATE SET consumed_at=EXCLUDED.consumed_at""",
                (
                    approval.approval_id, approval.run_id, approval.chunk_id,
                    Jsonb(approval.step_ids), approval.binding_digest, approval.decision,
                    approval.actor, approval.reason, approval.created_at, approval.consumed_at,
                ),
            )

    def supersede_approvals(self, run_id: str, chunk_id: str, at: str) -> int:
        with self._pool.connection() as connection:
            cursor = connection.execute(
                "UPDATE approvals SET consumed_at=%s WHERE run_id=%s AND chunk_id=%s AND consumed_at IS NULL",
                (at, run_id, chunk_id),
            )
            return cursor.rowcount

    def active_approval(self, run_id: str, chunk_id: str) -> Approval | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                """SELECT * FROM approvals WHERE run_id=%s AND chunk_id=%s
                AND decision='approved' AND consumed_at IS NULL
                ORDER BY created_at DESC, approval_seq DESC LIMIT 1""",
                (run_id, chunk_id),
            ).fetchone()
        return _approval(row) if row else None

    def list_approvals(self, run_id: str) -> list[Approval]:
        with self._pool.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM approvals WHERE run_id=%s ORDER BY created_at, approval_seq",
                (run_id,),
            ).fetchall()
        return [_approval(row) for row in rows]

    def append_audit(self, run_id: str, event: str, actor: str, detail: dict[str, Any] | None = None, step_id: str | None = None) -> AuditEvent:
        record = AuditEvent(
            event_id=new_id("evt"), run_id=run_id, event=event, actor=actor,
            detail=detail or {}, step_id=step_id,
        )
        with self._pool.connection() as connection:
            connection.execute(
                """INSERT INTO audit_events
                (event_id,run_id,step_id,event,actor,detail,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (
                    record.event_id, record.run_id, record.step_id, record.event,
                    record.actor, Jsonb(record.detail), record.created_at,
                ),
            )
        return record

    def list_audit(self, run_id: str) -> list[AuditEvent]:
        with self._pool.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM audit_events WHERE run_id=%s ORDER BY created_at, audit_seq",
                (run_id,),
            ).fetchall()
        return [
            AuditEvent(
                event_id=row["event_id"], run_id=row["run_id"], event=row["event"],
                actor=row["actor"], detail=row["detail"], step_id=row.get("step_id"),
                created_at=_iso(row["created_at"]),
            ) for row in rows
        ]

    # Execution ledger ----------------------------------------------------

    def claim_action_execution(self, envelope: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        now = now_iso()
        key = str(envelope["idempotency_key"])
        with self._pool.connection() as connection:
            row = connection.execute(
                """INSERT INTO action_executions
                (idempotency_key,run_id,chunk_id,step_id,approval_id,action,
                 action_version,catalog_version,connector,operation,envelope,status,
                 created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'claimed',%s,%s)
                ON CONFLICT(idempotency_key) DO NOTHING RETURNING *""",
                (
                    key, envelope["run_id"], envelope["chunk_id"], envelope["step_id"],
                    envelope["approval_id"], envelope["action"], envelope["action_version"],
                    envelope["catalog_version"], envelope["connector"], envelope["operation"],
                    Jsonb(envelope), now, now,
                ),
            ).fetchone()
            if row:
                return True, _execution(row)
            existing = connection.execute(
                "SELECT * FROM action_executions WHERE idempotency_key=%s", (key,)
            ).fetchone()
            if existing is None:
                raise RuntimeError("execution claim conflict produced no existing row")
            return False, _execution(existing)

    def get_action_execution(self, key: str) -> dict[str, Any] | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                "SELECT * FROM action_executions WHERE idempotency_key=%s", (key,)
            ).fetchone()
        return _execution(row) if row else None

    def complete_action_execution(self, key: str, outcome: str) -> None:
        with self._pool.connection() as connection:
            cursor = connection.execute(
                """UPDATE action_executions SET status='succeeded', outcome=%s,
                error=NULL, updated_at=%s WHERE idempotency_key=%s AND status='claimed'""",
                (outcome, now_iso(), key),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"execution '{key}' is not in claimed state")

    def fail_action_execution(self, key: str, error: str) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                """UPDATE action_executions SET status='failed', error=%s,
                updated_at=%s WHERE idempotency_key=%s AND status='claimed'""",
                (error[:1000], now_iso(), key),
            )

    def reclaim_stale_executions(self, timeout_seconds: int) -> list[dict[str, Any]]:
        """Fail executions stuck in 'claimed' past the timeout (crash recovery).

        A claim with no terminal outcome older than the timeout belonged to a
        worker that died mid-dispatch. Transition it to 'failed' -- never
        silently retry -- so the step surfaces for a human. Returns the
        reclaimed records. Uses UPDATE ... RETURNING so the find-and-fail is one
        atomic statement (no window where two reapers both act on a row).
        """

        from datetime import timedelta

        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)
        ).isoformat()
        with self._pool.connection() as connection:
            rows = connection.execute(
                """UPDATE action_executions
                SET status='failed',
                    error='reclaimed: worker did not finish before timeout',
                    updated_at=%s
                WHERE status='claimed' AND updated_at < %s
                RETURNING idempotency_key, run_id, step_id, updated_at""",
                (now_iso(), cutoff),
            ).fetchall()
        return [dict(r) for r in rows]

    def counts_by_state(self) -> dict[str, int]:
        with self._pool.connection() as connection:
            rows = connection.execute(
                "SELECT state, COUNT(*) AS n FROM runs GROUP BY state"
            ).fetchall()
        return {row["state"]: int(row["n"]) for row in rows}

    # Knowledge -----------------------------------------------------------

    def save_source(self, src: KnowledgeSource, content: str | None = None) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                """INSERT INTO knowledge_sources
                (source_id,filename,kind,file_format,size_bytes,sha256,uploaded_by,
                 uploaded_at,status,document,content)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(source_id) DO UPDATE SET status=EXCLUDED.status,
                document=EXCLUDED.document,
                content=COALESCE(EXCLUDED.content,knowledge_sources.content)""",
                (
                    src.source_id, src.filename, src.kind.value, src.file_format,
                    src.size_bytes, src.sha256, src.uploaded_by, src.uploaded_at,
                    src.status, Jsonb(src.to_dict()), content,
                ),
            )

    def get_source_content(self, source_id: str) -> str | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                "SELECT content FROM knowledge_sources WHERE source_id=%s", (source_id,)
            ).fetchone()
        return row["content"] if row else None

    def list_sources(self, kind: str | None = None) -> list[dict[str, Any]]:
        query, args = "SELECT document FROM knowledge_sources", []
        if kind:
            query += " WHERE kind=%s"
            args.append(kind)
        query += " ORDER BY uploaded_at DESC"
        with self._pool.connection() as connection:
            rows = connection.execute(query, args).fetchall()
        return [row["document"] for row in rows]

    def get_source(self, source_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                "SELECT document FROM knowledge_sources WHERE source_id=%s", (source_id,)
            ).fetchone()
        return row["document"] if row else None

    def find_source_by_hash(self, sha256: str) -> dict[str, Any] | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                "SELECT document FROM knowledge_sources WHERE sha256=%s LIMIT 1", (sha256,)
            ).fetchone()
        return row["document"] if row else None

    def _draft(self, document: dict[str, Any]) -> ProcedureDraft:
        return ProcedureDraft(
            draft_id=document["draft_id"], source_id=document["source_id"],
            procedure_id=document["procedure_id"], name=document["name"],
            payload=document["payload"], status=DraftStatus(document["status"]),
            validation_errors=list(document.get("validation_errors", [])),
            risk_warnings=list(document.get("risk_warnings", [])),
            reviewed_by=document.get("reviewed_by"), reviewed_at=document.get("reviewed_at"),
            review_note=document.get("review_note", ""), created_at=document["created_at"],
        )

    def save_draft(self, draft: ProcedureDraft) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                """INSERT INTO procedure_drafts
                (draft_id,source_id,procedure_id,status,created_at,document)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT(draft_id) DO UPDATE SET status=EXCLUDED.status,
                document=EXCLUDED.document""",
                (
                    draft.draft_id, draft.source_id, draft.procedure_id,
                    draft.status.value, draft.created_at, Jsonb(draft.to_dict()),
                ),
            )

    def get_draft(self, draft_id: str) -> ProcedureDraft | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                "SELECT document FROM procedure_drafts WHERE draft_id=%s", (draft_id,)
            ).fetchone()
        return self._draft(row["document"]) if row else None

    def list_drafts(self, status: str | None = None, source_id: str | None = None) -> list[ProcedureDraft]:
        clauses, args = [], []
        if status:
            clauses.append("status=%s")
            args.append(status)
        if source_id:
            clauses.append("source_id=%s")
            args.append(source_id)
        query = "SELECT document FROM procedure_drafts"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC"
        with self._pool.connection() as connection:
            rows = connection.execute(query, args).fetchall()
        return [self._draft(row["document"]) for row in rows]

    def publish_procedure(self, procedure_id: str, name: str, payload: dict[str, Any], published_by: str, source_id: str | None = None, draft_id: str | None = None) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                """INSERT INTO published_procedures
                (procedure_id,name,source_id,draft_id,published_by,published_at,enabled,payload)
                VALUES (%s,%s,%s,%s,%s,%s,TRUE,%s)
                ON CONFLICT(procedure_id) DO UPDATE SET name=EXCLUDED.name,
                source_id=EXCLUDED.source_id,draft_id=EXCLUDED.draft_id,
                published_by=EXCLUDED.published_by,published_at=EXCLUDED.published_at,
                enabled=TRUE,payload=EXCLUDED.payload""",
                (procedure_id, name, source_id, draft_id, published_by, now_iso(), Jsonb(payload)),
            )

    def list_published(self, only_enabled: bool = True) -> list[dict[str, Any]]:
        query = "SELECT * FROM published_procedures"
        if only_enabled:
            query += " WHERE enabled IS TRUE"
        query += " ORDER BY published_at DESC"
        with self._pool.connection() as connection:
            rows = connection.execute(query).fetchall()
        return [
            {
                "procedure_id": row["procedure_id"], "name": row["name"],
                "source_id": row.get("source_id"), "draft_id": row.get("draft_id"),
                "published_by": row["published_by"],
                "published_at": _iso(row["published_at"]),
                "enabled": bool(row["enabled"]), "payload": row["payload"],
            } for row in rows
        ]

    def set_published_enabled(self, procedure_id: str, enabled: bool) -> bool:
        with self._pool.connection() as connection:
            cursor = connection.execute(
                "UPDATE published_procedures SET enabled=%s WHERE procedure_id=%s",
                (enabled, procedure_id),
            )
            return cursor.rowcount > 0

    # Dictionary ----------------------------------------------------------

    def save_dictionary_entry(self, entry: DictionaryEntry) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                """INSERT INTO dictionary_entries
                (entry_id,source_id,canonical,enabled,created_at,document)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT(entry_id) DO UPDATE SET enabled=EXCLUDED.enabled,
                document=EXCLUDED.document""",
                (
                    entry.entry_id, entry.source_id, entry.canonical, entry.enabled,
                    entry.created_at, Jsonb(entry.to_dict()),
                ),
            )

    def list_dictionary(self, only_enabled: bool = True) -> list[DictionaryEntry]:
        query = "SELECT document FROM dictionary_entries"
        if only_enabled:
            query += " WHERE enabled IS TRUE"
        query += " ORDER BY canonical"
        with self._pool.connection() as connection:
            rows = connection.execute(query).fetchall()
        return [
            DictionaryEntry(
                entry_id=row["document"]["entry_id"],
                source_id=row["document"]["source_id"],
                canonical=row["document"]["canonical"],
                aliases=list(row["document"].get("aliases", [])),
                note=row["document"].get("note", ""),
                enabled=bool(row["document"].get("enabled", True)),
                created_at=row["document"]["created_at"],
            ) for row in rows
        ]

    def set_dictionary_enabled(self, entry_id: str, enabled: bool) -> bool:
        with self._pool.connection() as connection:
            row = connection.execute(
                "SELECT document FROM dictionary_entries WHERE entry_id=%s", (entry_id,)
            ).fetchone()
            if not row:
                return False
            document = dict(row["document"])
            document["enabled"] = enabled
            connection.execute(
                "UPDATE dictionary_entries SET enabled=%s,document=%s WHERE entry_id=%s",
                (enabled, Jsonb(document), entry_id),
            )
        return True

    def delete_dictionary_by_source(self, source_id: str) -> int:
        with self._pool.connection() as connection:
            cursor = connection.execute(
                "DELETE FROM dictionary_entries WHERE source_id=%s", (source_id,)
            )
            return cursor.rowcount

    # Live tickets --------------------------------------------------------

    def upsert_live_ticket(self, ticket: LiveTicket) -> bool:
        with self._pool.connection() as connection:
            row = connection.execute(
                """INSERT INTO live_tickets
                (live_id,ticket_number,sys_id,source,tbl,status,fetched_at,
                 sys_updated_on,run_id,document)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(source,sys_id) DO NOTHING RETURNING live_id""",
                (
                    ticket.live_id, ticket.ticket_number, ticket.sys_id, ticket.source,
                    ticket.table, ticket.status.value, ticket.fetched_at,
                    ticket.sys_updated_on, ticket.run_id, Jsonb(ticket.to_dict()),
                ),
            ).fetchone()
            return row is not None

    def _live(self, document: dict[str, Any]) -> LiveTicket:
        return LiveTicket(
            live_id=document["live_id"], ticket_number=document["ticket_number"],
            sys_id=document["sys_id"], source=document["source"], table=document["table"],
            payload=document["payload"], status=LiveTicketStatus(document["status"]),
            match=document.get("match", {}), run_id=document.get("run_id"),
            fetched_at=document["fetched_at"], sys_updated_on=document.get("sys_updated_on", ""),
        )

    def get_live_ticket(self, live_id: str) -> LiveTicket | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                "SELECT document FROM live_tickets WHERE live_id=%s", (live_id,)
            ).fetchone()
        return self._live(row["document"]) if row else None

    def update_live_ticket(self, ticket: LiveTicket) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                "UPDATE live_tickets SET status=%s,run_id=%s,document=%s WHERE live_id=%s",
                (ticket.status.value, ticket.run_id, Jsonb(ticket.to_dict()), ticket.live_id),
            )

    def list_live_tickets(self, status: str | None = None, limit: int = 100) -> list[LiveTicket]:
        query, args = "SELECT document FROM live_tickets", []
        if status:
            query += " WHERE status=%s"
            args.append(status)
        query += " ORDER BY fetched_at DESC, ingest_seq DESC LIMIT %s"
        args.append(limit)
        with self._pool.connection() as connection:
            rows = connection.execute(query, args).fetchall()
        return [self._live(row["document"]) for row in rows]

    def live_counts(self) -> dict[str, int]:
        with self._pool.connection() as connection:
            rows = connection.execute(
                "SELECT status,COUNT(*) AS n FROM live_tickets GROUP BY status"
            ).fetchall()
        return {row["status"]: int(row["n"]) for row in rows}

    def known_sys_ids(self, source: str) -> set[str]:
        with self._pool.connection() as connection:
            rows = connection.execute(
                "SELECT sys_id FROM live_tickets WHERE source=%s", (source,)
            ).fetchall()
        return {row["sys_id"] for row in rows}

    # Settings, cascade, credentials --------------------------------------

    def set_setting(self, key: str, value: Any) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                """INSERT INTO settings(key,value,updated_at) VALUES (%s,%s,%s)
                ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value,
                updated_at=EXCLUDED.updated_at""",
                (key, Jsonb(value), now_iso()),
            )

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._pool.connection() as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE key=%s", (key,)
            ).fetchone()
        return row["value"] if row else default

    def delete_source_cascade(self, source_id: str) -> dict[str, int]:
        with self._pool.connection() as connection:
            with connection.transaction():
                publications = connection.execute(
                    "DELETE FROM published_procedures WHERE source_id=%s", (source_id,)
                ).rowcount
                drafts = connection.execute(
                    "DELETE FROM procedure_drafts WHERE source_id=%s", (source_id,)
                ).rowcount
                entries = connection.execute(
                    "DELETE FROM dictionary_entries WHERE source_id=%s", (source_id,)
                ).rowcount
                sources = connection.execute(
                    "DELETE FROM knowledge_sources WHERE source_id=%s", (source_id,)
                ).rowcount
        return {
            "sources": sources, "drafts": drafts,
            "published_procedures": publications, "dictionary_entries": entries,
        }

    def save_credential(self, credential: dict[str, Any]) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                """INSERT INTO credentials
                (credential_id,name,connector,account,secret_ref,scope,permissions,
                 enabled,created_by,created_at,document)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(credential_id) DO UPDATE SET name=EXCLUDED.name,
                connector=EXCLUDED.connector,account=EXCLUDED.account,
                secret_ref=EXCLUDED.secret_ref,scope=EXCLUDED.scope,
                permissions=EXCLUDED.permissions,enabled=EXCLUDED.enabled,
                document=EXCLUDED.document""",
                (
                    credential["credential_id"], credential["name"], credential["connector"],
                    credential["account"], credential["secret_ref"],
                    Jsonb(credential.get("scope", [])), Jsonb(credential.get("permissions", [])),
                    bool(credential.get("enabled", True)), credential["created_by"],
                    credential["created_at"], Jsonb(credential),
                ),
            )

    def list_credentials(self) -> list[dict[str, Any]]:
        with self._pool.connection() as connection:
            rows = connection.execute(
                "SELECT document FROM credentials ORDER BY created_at DESC"
            ).fetchall()
        return [row["document"] for row in rows]

    def get_credential(self, credential_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                "SELECT document FROM credentials WHERE credential_id=%s", (credential_id,)
            ).fetchone()
        return row["document"] if row else None

    def delete_credential(self, credential_id: str) -> bool:
        with self._pool.connection() as connection:
            cursor = connection.execute(
                "DELETE FROM credentials WHERE credential_id=%s", (credential_id,)
            )
            return cursor.rowcount > 0
