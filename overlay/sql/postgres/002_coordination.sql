CREATE TABLE IF NOT EXISTS cache_revisions (
    name      TEXT PRIMARY KEY,
    revision  BIGINT NOT NULL CHECK (revision >= 0)
);
INSERT INTO cache_revisions(name, revision)
VALUES ('procedure_registry', 0)
ON CONFLICT(name) DO NOTHING;

CREATE OR REPLACE FUNCTION bump_procedure_registry_revision()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO cache_revisions(name, revision)
    VALUES ('procedure_registry', 1)
    ON CONFLICT(name) DO UPDATE
    SET revision = cache_revisions.revision + 1;
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS published_procedures_revision ON published_procedures;
CREATE TRIGGER published_procedures_revision
AFTER INSERT OR UPDATE OR DELETE ON published_procedures
FOR EACH STATEMENT EXECUTE FUNCTION bump_procedure_registry_revision();

DROP TRIGGER IF EXISTS dictionary_entries_revision ON dictionary_entries;
CREATE TRIGGER dictionary_entries_revision
AFTER INSERT OR UPDATE OR DELETE ON dictionary_entries
FOR EACH STATEMENT EXECUTE FUNCTION bump_procedure_registry_revision();

CREATE TABLE IF NOT EXISTS named_leases (
    name          TEXT PRIMARY KEY,
    owner_id      TEXT NOT NULL,
    fencing_token BIGINT NOT NULL CHECK (fencing_token > 0),
    expires_at    TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_named_leases_expiry ON named_leases(expires_at);
