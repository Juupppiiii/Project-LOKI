CREATE TABLE IF NOT EXISTS schema_migrations (
    version      INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    applied_at   TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS runs (
    run_id            TEXT PRIMARY KEY,
    ticket_id         TEXT NOT NULL,
    procedure_id      TEXT NOT NULL,
    assignment_group  TEXT,
    state             TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL,
    updated_at        TIMESTAMPTZ NOT NULL,
    document          JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_state ON runs(state);
CREATE INDEX IF NOT EXISTS idx_runs_group ON runs(assignment_group);
CREATE INDEX IF NOT EXISTS idx_runs_ticket ON runs(ticket_id);

CREATE TABLE IF NOT EXISTS approvals (
    approval_seq    BIGINT GENERATED ALWAYS AS IDENTITY,
    approval_id     TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    chunk_id        TEXT NOT NULL,
    step_ids        JSONB NOT NULL,
    binding_digest  TEXT NOT NULL,
    decision        TEXT NOT NULL,
    actor           TEXT NOT NULL,
    reason          TEXT,
    created_at      TIMESTAMPTZ NOT NULL,
    consumed_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_appr_run ON approvals(run_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_approval_active_chunk
    ON approvals(run_id, chunk_id) WHERE consumed_at IS NULL;

CREATE TABLE IF NOT EXISTS audit_events (
    audit_seq   BIGINT GENERATED ALWAYS AS IDENTITY,
    event_id    TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL,
    step_id     TEXT,
    event       TEXT NOT NULL,
    actor       TEXT NOT NULL,
    detail      JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_run ON audit_events(run_id);

CREATE TABLE IF NOT EXISTS knowledge_sources (
    source_id    TEXT PRIMARY KEY,
    filename     TEXT NOT NULL,
    kind         TEXT NOT NULL,
    file_format  TEXT NOT NULL,
    size_bytes   BIGINT NOT NULL,
    sha256       TEXT NOT NULL,
    uploaded_by  TEXT NOT NULL,
    uploaded_at  TIMESTAMPTZ NOT NULL,
    status       TEXT NOT NULL,
    document     JSONB NOT NULL,
    content      TEXT
);
CREATE INDEX IF NOT EXISTS idx_ks_kind ON knowledge_sources(kind);
CREATE INDEX IF NOT EXISTS idx_ks_sha256 ON knowledge_sources(sha256);

CREATE TABLE IF NOT EXISTS procedure_drafts (
    draft_id     TEXT PRIMARY KEY,
    source_id    TEXT NOT NULL,
    procedure_id TEXT NOT NULL,
    status       TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL,
    document     JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_draft_status ON procedure_drafts(status);
CREATE INDEX IF NOT EXISTS idx_draft_source ON procedure_drafts(source_id);

CREATE TABLE IF NOT EXISTS published_procedures (
    procedure_id  TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    source_id     TEXT,
    draft_id      TEXT,
    published_by  TEXT NOT NULL,
    published_at  TIMESTAMPTZ NOT NULL,
    enabled       BOOLEAN NOT NULL DEFAULT TRUE,
    payload       JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS dictionary_entries (
    entry_id   TEXT PRIMARY KEY,
    source_id  TEXT NOT NULL,
    canonical  TEXT NOT NULL,
    enabled    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL,
    document   JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dict_enabled ON dictionary_entries(enabled);

CREATE TABLE IF NOT EXISTS live_tickets (
    ingest_seq     BIGINT GENERATED ALWAYS AS IDENTITY,
    live_id        TEXT PRIMARY KEY,
    ticket_number  TEXT NOT NULL,
    sys_id         TEXT NOT NULL,
    source         TEXT NOT NULL,
    tbl            TEXT NOT NULL,
    status         TEXT NOT NULL,
    fetched_at     TIMESTAMPTZ NOT NULL,
    sys_updated_on TEXT,
    run_id         TEXT,
    document       JSONB NOT NULL,
    UNIQUE (source, sys_id)
);
CREATE INDEX IF NOT EXISTS idx_live_status ON live_tickets(status);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS credentials (
    credential_id TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    connector     TEXT NOT NULL,
    account       TEXT NOT NULL,
    secret_ref    TEXT NOT NULL,
    scope         JSONB NOT NULL,
    permissions   JSONB NOT NULL,
    enabled       BOOLEAN NOT NULL DEFAULT TRUE,
    created_by    TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL,
    document      JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cred_connector ON credentials(connector);

CREATE TABLE IF NOT EXISTS action_executions (
    execution_seq  BIGINT GENERATED ALWAYS AS IDENTITY,
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
    envelope        JSONB NOT NULL,
    status          TEXT NOT NULL,
    outcome         TEXT,
    error           TEXT,
    lease_owner     TEXT,
    lease_expires_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exec_run ON action_executions(run_id);
CREATE INDEX IF NOT EXISTS idx_exec_step ON action_executions(step_id);
CREATE INDEX IF NOT EXISTS idx_exec_status ON action_executions(status);
