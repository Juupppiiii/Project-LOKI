# BUCKY — Architecture

_Last updated: 2026-11-06_

BUCKY is a **human-in-the-loop ticket-automation platform**. It pulls real
ServiceNow tickets, matches them to codified procedures, and runs a governed
**propose → approve → execute → audit** lifecycle in which the agent only ever
performs bounded, approved, low-risk steps and a human owns everything
irreversible. A read-only **triage agent** can additionally investigate a
matched monitoring ticket and draft a recommendation — gathering evidence only,
never acting.

This document describes what exists today. It is explicit about three states:

- ✅ **Real & tested** — built, covered by the test suite, exercised.
- 🟡 **Built but dormant** — code exists and is tested, but off by default and
  not yet exercised against a real external system.
- 🔴 **Scaffolded, not deployed** — written (Terraform / Docker) but nothing is
  live.

---

## The one principle everything serves

**The agent is granted work at the _step_ level, never the _ticket_ level.** A
ticket moves back and forth between agent and human; neither owns the whole
thing. Every control below exists to keep that promise. The triage agent is a
second expression of the same principle: it is granted **read-only evidence
gathering**, never a remediation step.

---

## The two data planes

BUCKY has two pipelines that never share a runtime store. The only coupling is
that the overlay reads `src/config.py` for ServiceNow connection settings.

### Plane 1 — Offline discovery (`src/`, `scripts/01`–`04`) ✅
Pulls ServiceNow exports into DuckDB and mines which repetitive work is worth
automating. Read-only, offline analytics, run with `python src/main.py`.

- `src/config.py` — table/field definitions and ServiceNow connection config.
- `src/extractor.py` — paged, read-only ServiceNow extraction to CSV.
- `src/analyzer.py` — scores repetitive, automatable work. (Also contains the
  example work-note author name "Stephen Mullan" in a comment — preserved.)
- `src/database.py` — loads the CSVs into a single DuckDB file for analysis.
- `src/main.py` — the pipeline orchestrator (imports only config/extractor/
  analyzer/database).
- `src/dashboard.py` — optional Streamlit view over the analyzer output
  (`streamlit run src/dashboard.py`); not part of the pipeline import graph.

Finding of record: of ~22k closed incidents pulled, roughly three-quarters
carried a substantive resolution; the realistic yield is a modest set of
codifiable procedures, not thousands.

### Plane 2 — Live runtime overlay (`overlay/`, `system2/`) ✅
The FastAPI service + React UI that runs the governed lifecycle against live,
read-only ServiceNow ticket data. This is "BUCKY" in normal use. It shares no
store with the discovery plane and never writes back to ServiceNow from the
live feed.

---

## System 2 — Procedure engine (`system2/`) ✅

The domain-agnostic rulebook, imported into the overlay through
`overlay/_system2.py` (a sys.path shim, not dead code despite the leading
underscore).

| Module | Role |
|---|---|
| `schema.py` | Procedure / step / trigger schema + `load_procedures()` validation |
| `engine.py` | Keyword match + scoring + parameter extraction (hostname, instance_id, queue_name, service, username) from alert text |
| `executor.py` | Render / simulate a procedure's steps; real execution is hard-disabled |
| `run_engine.py` | Thin CLI around match → render for local trials |
| `draft_procedures.py` | Offline drafting of candidate procedures from mined patterns |

Built-in procedures live in `system2/procedures.json` (16 procedures: Wintel,
Linux, network, voice, identity, trading, compliance, desktop). Published
procedures can also come from the database via the registry.

---

## Overlay module map (`overlay/`)

| Module | Role | State |
|---|---|---|
| `api.py` | FastAPI transport; the only contract clients speak | ✅ |
| `service.py` | Application authority — every policy decision | ✅ |
| `domain.py` | Pure model: runs, steps, chunks, approvals, audit, live tickets | ✅ |
| `risk.py` | Default-deny risk classification (R0–RX) | ✅ |
| `planner.py` | Ticket + procedure → approvable plan + binding digest | ✅ |
| `registry.py` | Effective rule set: built-in + DB-published procedures | ✅ |
| `_system2.py` | sys.path shim importing the System 2 engine | ✅ |
| `store.py` | SQLite repository (runs, approvals, idempotency, audit, live tickets, triage runs) | ✅ |
| `postgres_store.py` | PostgreSQL repository (same contract) | 🟡 built, SQLite is the default |
| `store_contract.py` | The repository interface both stores implement | ✅ |
| `store_factory.py` | Picks the backend from `BUCKY_STORE_BACKEND` | ✅ |
| `coordination.py` | Named leases / leader election for the poller | ✅ |
| `action_catalog.py` | Immutable, versioned action allowlist | ✅ |
| `connectors.py` | Approval-bound envelope + result contract | ✅ |
| `action_gateway.py` | Default-deny dispatch; simulation + scoped live mode | ✅ |
| `connectors_servicenow.py` | First real connector (work notes), dual-switched | 🟡 dormant |
| `simulator.py` | Simulated step execution (the safe default path) | ✅ |
| `rollback.py` | Compensation seam for executed steps | ✅ |
| `verification.py` | Post-execution postcondition checks (fail-closed on real, unverifiable results) | ✅ |
| `live.py` | Read-only ServiceNow polling (GET only) | ✅ |
| `efficiency.py` | Advisory resolution-time + automatability ranking | ✅ |
| `semantic_engine.py` | Advisory meaning-based matching | ✅ |
| `knowledge.py` | Handbook/dictionary upload + draft/publish | ✅ |
| `knowledge_base.py` | Retrieval of solved-ticket grounding (RAG) | ✅ |
| `knowledge_llm.py` | LLM backend seam (Bedrock Converse + offline echo) | ✅ |
| `diagnostics.py` | Read-only diagnostic gateway: fixed op catalogue, validation, allowlists, mock backend | ✅ |
| `diagnostics_ssm.py` | Real read-only backend over AWS SSM Run Command (fixed read-only PowerShell) | 🟡 dormant (third switch) |
| `triage_agent.py` | Tier A read-only triage agent: bounded investigation loop + planner | ✅ |
| `credentials.py` | OS-credential-store integration (no secrets in the DB) | ✅ |
| `auth.py` | Entra OIDC verification + sessions; capability RBAC | ✅ (enforcement off by default) |
| `worker.py` | SQS → execute → Step Functions worker loop | 🟡 built, not the default path |
| `worker_main.py` | ECS worker entrypoint (`python -m overlay.worker_main`) | 🟡 |
| `worker_aws.py` | AWS transport (SQS/Step Functions) for the worker | 🟡 |
| `worker_transport.py` | Transport abstraction for the worker | 🟡 |
| `ui/` | React/Vite app (Queue / Investigate / Knowledge / Live) | ✅ |
| `web/` | Dependency-free fallback client, served at `/legacy` and `/static` | ✅ |

---

## The request lifecycle (remediation runs)

```
ticket ──► service.propose ──► planner.build_plan ──► store (run, awaiting approval)
                                   │
                                   ├─ risk.classify every step (default-deny)
                                   └─ group into approval chunks + binding digest

human ──► service.decide_chunk (approve) ──► store (Approval, bound to a digest)

human ──► service.execute_chunk ──► guards: active approval? digest match? in order?
                                             separation of duties? not expired?
                                   │
                                   ├─ build_envelope (idempotency key)
                                   ├─ store.claim_action_execution (claim-before-dispatch)
                                   └─ action_gateway.dispatch ──► connector (simulation|live)
                                             │
                                             ├─ verification.check (fail-closed)
                                             └─ store.complete/fail + append-only audit
```

Every transition writes an **append-only audit event** distinguishing
`actor=agent`, `triggered_by`, and `approved_by`.

---

## Tier A — the read-only triage agent

A bounded, agentic investigation layer that sits **above** the diagnostic
gateway and **below** human approval. It gathers read-only evidence and drafts a
recommendation; it executes nothing and authorises nothing.

```
matched ticket ──► triage_agent.run
     │
     ├─ parse_alert            (host + alert kind from the CheckMK text)
     ├─ loop (bounded by max steps):
     │     planner.next_step ──► one read-only tool from a FIXED allowlist
     │         │                 (check_connectivity, resolve_host, service_status,
     │         │                  disk_free, recent_events, correlate_neighbors,
     │         │                  check_change_window, similar_history, finish)
     │         └─ run it through diagnostics.DiagnosticGateway (single read path)
     └─ summarise ──► TriageVerdict
            { classification, confidence, DRAFT work note, disposition, routed_to_human }
```

Properties:

- **Read-only by construction.** Every host-touching tool delegates to
  `diagnostics.py`, whose backend protocol has a single `read` method and no
  write path. The agent drafts a work note; a human posts it.
- **Planner is swappable, scope is fixed.** Offline it uses a deterministic
  rule-based planner; with `BUCKY_LLM_ENABLED=1` it uses a Bedrock Converse
  **tool-use** planner. Either way the tool allowlist is identical — the planner
  only changes *how* the next read is chosen, and a model failure falls back to
  the rule-based planner rather than aborting.
- **Fail safe, not false-positive.** If a host is still unreachable or evidence
  is weak, it classifies `still_down_needs_human` / `insufficient_evidence` and
  routes to a human — it never recommends closing a ticket it could not
  substantiate read-only.
- **The real host channel** (`diagnostics_ssm.py`) runs a fixed, read-only
  PowerShell snippet per operation via SSM Run Command against an allowlisted
  managed node. It is gated by a **third** switch on top of the execution
  dual-switch and fails closed.

Triage runs are persisted in the dedicated `triage_runs` table with the full
read-only tool transcript, and surfaced in the UI's **Investigate** tab
(`Run triage` → verdict + draft note + transcript).

---

## Safety model (all server-side, all tested)

- **Simulation is the floor.** `BUCKY_EXECUTION_MODE=simulation` is the default
  and the gateway refuses to construct a live dispatch in any other mode unless
  `live` is deliberately selected. Any value other than `simulation`/`live`
  fails at startup.
- **Dual / triple switches for anything real.** A real remediation connector
  needs `live` mode **and** its own write switch (e.g.
  `BUCKY_SERVICENOW_WRITE_ENABLED`). The real diagnostic backend needs `live`
  **and** `BUCKY_DIAGNOSTICS_ENABLED` **and** `BUCKY_SSM_DIAGNOSTICS_ENABLED`
  plus a host allowlist. Off at every layer by default.
- **Default-deny risk.** Every step is R0/R1/R2/R3/RX. The agent may run only
  R0/R1/R2 it was explicitly approved for; R3/RX are human-only. `run_command`,
  `reboot`, and `delete_profile` are deliberately human-only.
- **Two-layer enforcement.** `risk.py` classifies and `action_gateway.py`
  independently refuses anything not on the versioned allowlist.
- **Approval binding.** An approval is bound by digest to exactly what was shown
  (procedure version, steps, rendered text, parameters, action version,
  connector operation). Editing a parameter invalidates it.
- **Idempotency.** A durable key is claimed before dispatch; a replayed
  execution returns the stored outcome instead of running twice.
- **Separation of duties.** The approver of a chunk cannot also trigger it
  (`BUCKY_ENFORCE_SOD`, on by default).
- **Approval expiry.** Stale approvals are refused
  (`BUCKY_APPROVAL_TTL_SECONDS`, default 3600s).
- **Capability RBAC.** `auth.py` maps Entra roles to capabilities
  (viewer / operator / approver / admin); every write and the triage/diagnostic
  actions are gated server-side.
- **Post-execution verification.** `verification.py` confirms a result's
  postcondition; a real (non-simulated) result with no registered check returns
  `unverified` — fail-closed, never a false success.

---

## Advisory intelligence (recommends, never acts)

These features **enrich and recommend** but never approve, execute, reorder, or
skip. The keyword matcher stays the authoritative pick; a human stays the
approver.

| Feature | Endpoint | What it does |
|---|---|---|
| Semantic suggestions | `POST /api/suggest` | Meaning-based procedure hints when keyword match misses |
| Efficiency ranking | on `POST /api/match` | Ranks procedures by historical resolution time + automatability |
| Plan efficiency advice | `GET /api/runs/{id}/efficiency-advice` | Per-step agent/human annotation + handoff point; never mutates the plan |
| LLM step recommendation | `POST /api/recommend-steps` | Claude (Opus 5 on Bedrock) synthesises a critiqued, cited recommendation grounded in real solved tickets (RAG) |
| Evidence pack | `GET /api/live/tickets/{id}/evidence-pack` | Provenance-separated case file (facts, history, inference, diagnostics, feedback) |
| Recommendation feedback | `POST /api/recommendation-feedback` | Durable human validation of recommendations, separate from raw closure notes |

Retrieval (real grounding) is separated from synthesis so the grounding is
inspectable and the model can never fabricate its evidence. The LLM is off by
default (`BUCKY_LLM_ENABLED=0`) and never makes a paid/network call unprompted.
Verified live against `eu.anthropic.claude-opus-5` in eu-west-1.

---

## Storage

Two backends behind one repository contract (`store_contract.py`), selected by
`BUCKY_STORE_BACKEND` via `store_factory.py`.

| | SQLite (`store.py`) ✅ default | PostgreSQL (`postgres_store.py`) 🟡 |
|---|---|---|
| Schema version | `schema_version = 5` | `REQUIRED_SCHEMA_VERSION = 4` |
| Applied via | `CREATE TABLE IF NOT EXISTS` on open + additive `_migrate()` | `overlay/sql/postgres/001_initial.sql … 004_triage_runs.sql` |
| Scope | single host / local / simulation | shared, multi-user production target |

The two version numbers are **independent counters, not a contradiction** —
SQLite's v5 and Postgres's v4 are separate lineages that converge on the same
logical schema. Both carry the `triage_runs` table (indexed by `live_id`,
`ticket_ref`, and `classification` on SQLite), alongside `runs`, `approvals`,
`audit_events`, `action_executions`, `published_procedures`, `procedure_drafts`,
`knowledge_sources`, `dictionary_entries`, `live_tickets`,
`recommendation_feedback`, `settings`, `credentials`, `cache_revisions`, and
`named_leases`. The SQLite→PostgreSQL migration tool
(`scripts/migrate_sqlite_to_postgres.py`, dry-run by default) copies every table
including `triage_runs`.

---

## AWS deployment shape (`infrastructure/`, Terraform) 🔴 scaffolded, gated

```
            internal ALB (TLS, ACM)
                   │
            ECS Fargate: API task  ──┐
            ECS Fargate: worker task ┤──► Aurora PostgreSQL Serverless v2 (via RDS Proxy)
                   │                  │
            Step Functions ◄──► SQS FIFO queue (+ DLQ)
                   │
            ECR (immutable images) · CloudWatch · Bedrock (advisory) · SSM (read-only diagnostics)
```

- **IaC:** Terraform under `infrastructure/` (modules: `iam`, `ecs-service`,
  `rds`, `stepfunctions`, …), applied per the `pace/manila` convention. Optional
  IAM policies gate Bedrock (`enable_bedrock`) and read-only SSM diagnostics
  (`enable_ssm_diagnostics`, scoped to the `AWS-RunPowerShellScript` document and
  explicit target instances).
- **Image:** a two-stage `Dockerfile` — React/Vite build → slim Python 3.12
  runtime on port 8800, running as a non-root `bucky` user. The image ships the
  small tracked `sample_data/incidents.csv` (never the git-ignored real data).
- **CI:** `.gitlab-ci.yml` runs lint (ruff, advisory), the full pytest suite,
  `terraform fmt`/`validate`, and a Docker build; `deploy-dev` is a **manual**
  gate.
- **Status:** synthesizes/validates cleanly; **nothing deployed**. The service
  stays single-task and simulation-only until the Aurora repository, multi-write
  transactions, and claim recovery are production-hardened. Remaining blockers
  are largely organizational (account/VPC/cert/bootstrap), not code.

---

## Configuration (key toggles)

| Variable | Default | Purpose |
|---|---|---|
| `BUCKY_EXECUTION_MODE` | `simulation` | `simulation` or `live`; anything else fails at startup |
| `BUCKY_STORE_BACKEND` | `sqlite` | `sqlite` local, `postgres` production |
| `BUCKY_ENFORCE_AUTH` | `0` | Enforce Entra session on write routes |
| `BUCKY_ENFORCE_SOD` | `1` | Approver cannot also trigger execution |
| `BUCKY_APPROVAL_TTL_SECONDS` | `3600` | Approval expiry |
| `BUCKY_LLM_ENABLED` | `0` | Enable the Bedrock advisory model + triage planner |
| `BEDROCK_MODEL_ID` | `eu.anthropic.claude-opus-5` | Advisory model (inference profile id) |
| `BUCKY_TRIAGE_AGENT_ENABLED` | `0` | Enable the read-only triage agent |
| `BUCKY_SERVICENOW_WRITE_ENABLED` | `0` | Second switch for the real work-note connector |
| `BUCKY_DIAGNOSTICS_ENABLED` | `0` | Second switch for the real diagnostic gateway |
| `BUCKY_SSM_DIAGNOSTICS_ENABLED` | `0` | Third switch for the real SSM read-only backend |

---

## Honest status line

- **Functionally**: rich and tested — full remediation lifecycle, governance,
  advisory AI, and a read-only triage agent, all exercised by the suite
  (202 passed / 10 skipped) and live against Opus 5.
- **Operationally**: not yet deployed; auth enforcement off; running on SQLite
  locally; one real remediation connector and the real SSM diagnostic backend
  built but dormant behind their switches.
- **The gap to company use** is operational scaffolding (deploy, enforced auth
  wired to Entra, a real connector against a sandbox, the Aurora store, secrets
  management, observability) — most of which needs organizational inputs, not
  more code.
