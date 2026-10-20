-- Human-reviewed recommendation outcomes.
-- Kept separate from raw ServiceNow closure notes so explicit engineer
-- validation is the higher-quality evaluation and learning signal.

CREATE TABLE IF NOT EXISTS recommendation_feedback (
    feedback_seq BIGINT GENERATED ALWAYS AS IDENTITY,
    feedback_id  TEXT PRIMARY KEY,
    ticket_ref   TEXT NOT NULL,
    live_id      TEXT,
    procedure_id TEXT,
    verdict      TEXT NOT NULL,
    created_by   TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL,
    document     JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feedback_ticket
    ON recommendation_feedback(ticket_ref);
CREATE INDEX IF NOT EXISTS idx_feedback_procedure
    ON recommendation_feedback(procedure_id);
CREATE INDEX IF NOT EXISTS idx_feedback_verdict
    ON recommendation_feedback(verdict);
