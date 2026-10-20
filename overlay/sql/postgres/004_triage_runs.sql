-- Tier A triage-agent runs: a read-only, bounded investigation of a matched
-- monitoring ticket. Stored separately from remediation `runs` because a triage
-- run executes NOTHING -- it gathers evidence and drafts a recommendation that a
-- human approves. The full read-only tool transcript lives in `document`.

CREATE TABLE IF NOT EXISTS triage_runs (
    triage_seq      BIGINT GENERATED ALWAYS AS IDENTITY,
    triage_id       TEXT PRIMARY KEY,
    live_id         TEXT,
    ticket_ref      TEXT NOT NULL,
    host            TEXT,
    alert_kind      TEXT,
    classification  TEXT NOT NULL,
    routed_to_human BOOLEAN NOT NULL DEFAULT FALSE,
    simulated       BOOLEAN NOT NULL DEFAULT TRUE,
    created_by      TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL,
    document        JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_triage_live ON triage_runs(live_id);
CREATE INDEX IF NOT EXISTS idx_triage_ticket ON triage_runs(ticket_ref);
CREATE INDEX IF NOT EXISTS idx_triage_class ON triage_runs(classification);
