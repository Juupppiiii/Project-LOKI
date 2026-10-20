# BUCKY test suite

Automated tests across every layer. External systems (ServiceNow, Active
Directory, AWS, PostgreSQL) are mocked or auto-skipped, so the default run needs
no credentials and touches no real infrastructure.

## Install and run

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
```

Current baseline on a clean machine: **52 passed, 10 skipped**. The 10 skips are
the PostgreSQL-backed tests; they run only when a test database is provided.

## Layers

| Layer | Files | Needs | Runs by default |
|---|---|---|---|
| Unit / property | `test_action_catalog.py`, `test_risk_policy.py` | nothing | yes |
| Coordination | `test_coordination.py` | nothing (two SQLite connections) | yes |
| Repository | `test_repository.py` | SQLite always; PostgreSQL optional | SQLite yes |
| Migration | `test_migration.py` | SQLite always; full apply needs PostgreSQL | dry-run yes |
| Connector contract | `test_connector_contract.py` | nothing | yes |
| Integration | `test_integration_flow.py` | nothing | yes |
| Concurrency | `test_concurrency.py` | nothing; heavy race needs PostgreSQL | SQLite yes |
| CDK templates | `infra/test/template.test.mjs` | Node toolchain (`infra/`) | via `npm test` |

## Run one layer

```powershell
python -m pytest -m unit
python -m pytest -m repository
python -m pytest -m integration
python -m pytest -m concurrency
python -m pytest tests/test_migration.py
```

## PostgreSQL layers

Set a throwaway database DSN, then run again â€” the skipped tests execute and
cover the concurrent backend, the full SQLite->PostgreSQL migration, and the
high-contention execution-claim race:

```powershell
$env:BUCKY_TEST_POSTGRES_DSN = "postgresql://user:pass@localhost:5432/bucky_test"
python -m pytest -m repository
python -m pytest -m migration
python -m pytest -m concurrency
```

The PostgreSQL tests truncate their tables first, so point the DSN at a
disposable database only.

## CDK template assertions

From `infra/` (needs the Node toolchain and the corporate Nexus registry):

```powershell
npm ci
npm test
```

These assert the deployment gate: with the service disabled there is no ECS
service, load balancer, or DNS record; with it enabled the service enforces
PostgreSQL, simulation mode, authentication, secure cookies, an HTTPS listener,
and an `/api/ready` health check.

## What each layer proves

- **Unit** â€” versioned action resolution, default-deny risk, human-only
  arbitrary/destructive actions.
- **Coordination** â€” one lease owner at a time, fencing-token progression,
  monotonic registry revision across connections.
- **Repository** â€” run/approval/audit/execution/dictionary round-trips, source
  cascade, readiness, and SQLite/PostgreSQL parity.
- **Migration** â€” deterministic inventory and checksums; full copy+verify on
  PostgreSQL.
- **Connector contract** â€” the default-deny checks every connector must pass:
  approval binding, catalogue match, idempotency key, resolved parameters,
  simulation-only mode.
- **Integration** â€” propose -> approve -> execute -> audit, digest invalidation,
  refusal without approval, duplicate-run and double-execution prevention.
- **Concurrency** â€” exactly-once execution claims and single-poller leadership
  with failover.
- **CDK** â€” the service deployment gate is off by default and correct when on.

## Still requires real environments (not covered here)

These need the live systems and cannot be exercised locally:

- real PostgreSQL/Aurora integration beyond the optional DSN tests;
- real connector behavior against ServiceNow / Active Directory / vCenter;
- Entra token signature (JWKS) validation against the identity provider;
- deployed AWS behavior (only template synthesis is asserted here).
