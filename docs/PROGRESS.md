# Phase 1 Discovery Framework â€” Progress

_Last updated: 2026-09-28_

A DevOps Capability Enablement platform that pulls ServiceNow data, discovers
automation opportunities, and surfaces them via analysis + a dashboard.

Three systems, each building on the last:

| | System | Role | Status |
|---|--------|------|--------|
| 1 | Discovery | What work is repetitive and worth automating | Done |
| 2 | Procedure engine | How that work gets done (the rulebook) | Done |
| 3 | Task overlay | Who does which step, with human approval | Phase 2 done |

**Execution is simulation only** in all three. Ticket data can now be read live
from ServiceNow (read-only), which does not change that: nothing in this repo
writes to, or acts on, a target system.

## Pipeline

```
SYSTEM 1 â€” discovery
  .env â†’ config.py â†’ extractor.py â†’ data/*.csv â†’ analyzer.py â†’ output/*.csv
                                                     â†“
                                     database.py â†’ automation_discovery.duckdb
                                                     â†“
                                               dashboard.py (Streamlit)

SYSTEM 2 â€” procedures (learned from System 1 resolutions)
  data/*.csv â†’ draft_procedures.py â†’ procedures_drafts.json
                                          â†“  (human review + promotion)
                                     procedures.json
                                          â†“
                              engine.py (match) â†’ executor.py (render/simulate)

SYSTEM 3 â€” task overlay (who does which step)
  CSV tickets â”€â”
  live.py  â”€â”€â”€â”€â”¤ (read-only ServiceNow poll)
               â†“
  registry.py (built-in + published procedures, + dictionary aliases)
               â†“
  engine match â†’ planner.py (risk + chunks) â†’ store.py (SQLite)
               â†“
  api.py  â†â†’  web/ (browser) or any future client
               â†“
  approval â†’ simulator.py â†’ audit trail â†’ human handoff

  uploads: knowledge.py â†’ drafts â†’ [human publishes] â†’ registry
```

## Components

| File | Role | Status |
|------|------|--------|
| `config.py` | Single source of truth: creds, tuning, per-table fields + queries | Done |
| `extractor.py` | Paginated, retrying ServiceNow extractor | Done |
| `main.py` | Stage orchestrator (`--only`, `--skip-*` flags) | Done |
| `analyzer.py` | Summary, tasks, groups, scoring, resolution-text mining | Done |
| `database.py` | Loads CSVs into DuckDB | Done |
| `dashboard.py` | Streamlit view of automation candidates | Done (basic) |

## Key decisions & findings

1. **Credentials fixed** â€” `.env` was misnamed (`gg .env`) and used
   `SERVICENOW_*` names while code read `SN_*`. Config now accepts both;
   login is automatic (no prompt).
2. **"Manual tickets" = `contact_type != Automated`.** Of ~604k 3-month
   incidents, only **11,603** are human-raised (Email, Phone, IM, Walk-in,
   Self-service, Virtual Agent). The rest are the `svcgbsnsoap` / GATE Import
   machine feed.
3. **Pagination bug fixed** â€” extractor used to stop on a short-but-non-empty
   page (caused 4,309 / 27,069 truncations). Now pages against the
   authoritative stats count and stops only when complete or truly empty.
4. **Resolution content lives in `close_notes`** (~70% filled) and
   `work_notes` â€” NOT in `resolution_code` / `actions_taken` / `cause`
   (all empty on this instance).

## Data currently extracted (3-month window)

| Table | Rows |
|-------|------|
| incidents (manual only) | 11,603 |
| request_items | 7,824 |
| requests | 7,519 |
| changes | 4,649 |
| catalog_tasks | 3,432 |
| change_tasks | 2,969 |
| problem_tasks | 203 |
| problems | 117 |

## Outputs generated (`output/`)

- `dataset_summary.csv`
- `top_tasks.csv`
- `top_assignment_groups.csv`
- `top_automation_opportunities.csv`
- `resolution_summaries.csv` (how manual incidents were fixed)
- `resolution_keywords.csv`
- `automation_candidates.csv` (dashboard view)

## How to run

```powershell
python src/main.py                 # full: extract â†’ analyze â†’ load
python src/main.py --skip-extract  # re-analyze existing CSVs
python src/main.py --only extract  # single stage
streamlit run src/dashboard.py     # dashboard
```

## Open / next steps (System 1)

- [ ] Semantic grouping of near-duplicate resolutions (e.g. "reset password"
      vs "password reset")
- [ ] Add a resolution-patterns view to the dashboard
- [ ] Decide whether other tables need a "manual" filter (signal differs per table)
- [ ] Clean up diagnostic scripts (preflight, counts, test_extract,
      discover_manual, distribution, check_resolution_fields)
- [ ] Rotate the ServiceNow password (it was pasted in chat)

---

# System 2 â€” Universal Execution Engine (SIMULATION ONLY)

Reads structured *procedures* (the rules gathered from System 1 resolutions),
matches a ticket to one, and runs it in a safe mode. The engine is
domain-agnostic; all knowledge lives in `system2/procedures.json`.

**No live execution.** No connectors, no credentials, no network calls.
`LIVE_EXECUTION_ENABLED = False` and `live_execute()` raises.

## Files (`system2/`)

| File | Role |
|------|------|
| `schema.py` | Procedure / Step / Parameter / Trigger model + validating loader |
| `procedures.json` | The rule set (6 Wintel procedures) |
| `engine.py` | Match ticket â†’ procedure; extract parameters |
| `executor.py` | Layer A runbook, Layer B dry-run; Layer C hard-disabled |
| `run_engine.py` | CLI: `--list`, `--desc/--ci`, `--sample N`, `--mode` |
| `draft_procedures.py` | Auto-draft new procedures from resolution clusters |
| `procedures_drafts.json` | Draft procedures awaiting human review (not loaded) |

## Execution layers

- **A. runbook** â€” render an ordered, parameter-filled checklist for a human.
- **B. dry_run** â€” simulate each step with mock outcomes. Nothing real touched.
- **C. live** â€” DISABLED. Requires connectors + approval gate + rollback; a
  separate, deliberate future project.

## Accuracy (against 907 real Wintel incidents)

- Triggers match only the PROBLEM text, never resolution notes (this fixed
  massive over-matching). Minimum score threshold `SYS2_MIN_SCORE=3`.
- 6 procedures â†’ **97/907 matched**, all high-confidence, ~86% params resolved.
- Coverage grows as more procedures are promoted from drafts.

## Procedures so far

`disk_cleanup`, `server_reboot`, `service_restart`, `delete_old_profile`,
`vcenter_health_check`, `high_cpu` (promoted from auto-draft).

## How to run

```powershell
cd system2
python run_engine.py --list
python run_engine.py --desc "TPVOL ldn2ws9696 C Disk Space" --ci ldn2ws9696
python run_engine.py --sample 5 --mode runbook
python draft_procedures.py --min-frequency 5   # generate new drafts
```

## Open / next steps (System 2)

- [ ] Review + promote remaining drafts (login_access, high_memory_swap);
      narrow packet_loss / hardware_crit keywords first
- [ ] Add procedures for groups beyond Wintel
- [ ] Small review UI for drafts
- [ ] Layer C (real connectors + guardrails) â€” only as a separate project
```

---

# System 3 â€” Task Overlay (SIMULATION ONLY)

The human-in-the-loop layer. System 2 could say *"this ticket matches disk
cleanup, here are the 7 steps"* but had no state, no risk model, no approval and
no memory. The overlay adds exactly that.

**Core principle: the agent is granted work at the _step_ level, never at the
ticket level.** A ticket moves back and forth between agent and human, and
neither owns the whole thing.

## Files (`overlay/`)

| File | Role |
|------|------|
| `domain.py` | Pure model: risk classes, states, `TaskRun`, `StepRun`, `Chunk`, `Approval`, `AuditEvent`. No framework, no DB, no transport. |
| `risk.py` | Classification policy. Default-deny. |
| `planner.py` | Ticket + procedure â†’ approvable plan; chunking; binding digest |
| `store.py` | SQLite behind a repository seam (the only module that knows SQL) |
| `simulator.py` | Simulated step execution; where connectors will eventually plug in |
| `service.py` | Every use case; the authority on all rules |
| `api.py` | FastAPI; the contract every client speaks |
| `knowledge.py` | Parses uploaded handbooks/dictionaries into reviewable drafts |
| `registry.py` | The effective rule set: built-in + published, plus dictionary aliases |
| `live.py` | Read-only ServiceNow polling and ingest |
| `web/` | Thin browser client (Queue / Knowledge / Live tabs) â€” holds no business logic |
| `examples/` | Sample handbooks and a dictionary that upload as-is |
| `_system2.py` | The single place the overlay reaches into System 2 |
| `README.md` | Run steps, upload formats, live setup, safety controls, limitations |

## Risk model (default-deny)

| Class | Meaning | Agent may run |
|-------|---------|---------------|
| `R0` | Read-only observation | Yes, with chunk approval |
| `R1` | Low impact, additive/reversible | Yes, with chunk approval |
| `R2` | Real operational change | Yes, with its **own** step approval |
| `R3` | High impact or destructive | No â€” human only |
| `RX` | Unclassified | No â€” human only |

`run_command` maps to `RX` deliberately: a read-only `Get-PSDrive` and a
destructive `Remove-Item` are the same action, so the action alone cannot
justify execution. Anything unclassified stays with a human.

This is why `wintel.disk_cleanup` splits itself into:

- steps 1â€“2 â†’ agent (`connect`, `check`, R0)
- steps 3â€“5 â†’ **human** (`run_command`, RX)
- steps 6â€“7 â†’ agent (`check`, `notify`, R0/R1)

That is the "AI does a bounded chunk, human takes the complex part" model
working in practice.

## Safety controls (all enforced server-side, all tested)

| Control | Behaviour |
|---------|-----------|
| No execution without approval | Refused; the attempt is audited |
| Human-owned chunks can't be approved for the agent | Refused |
| Approval binds to what was shown | Digest over procedure version, steps, rendered text, parameters |
| Editing parameters invalidates approval | Digest changes â†’ re-approval required |
| One active approval per chunk | Prior approvals superseded on a new decision |
| Steps run in order | Refuses to jump ahead of outstanding work |
| `R2` needs individual approval | Cannot hide inside a low-risk chunk |
| `R3`/`RX` never agent-executed | Refused by policy, again by the simulator |
| Unresolved `<param?>` blocks the step | Hands off to a human |
| Manual completion requires evidence | Empty evidence refused |
| Audit is append-only | No update or delete path |
| No accidental ticket forks | A second run for an in-progress ticket is refused (`409 duplicate_run`) naming the existing run; `?allow_duplicate=true` overrides deliberately |

Audit distinguishes three things: `actor=agent` (what acted), `triggered_by`
(who clicked), `approved_by` (who authorised). A deliberate duplicate is
recorded on **both** runs, so either audit trail leads to the other.

## Data-format finding (affects System 1 and 2, not just the overlay)

`data/incidents.csv` stores ServiceNow **reference fields as objects**, not
names:

```
assignment_group = @{display_value=Global Wintel Server Support; link=https://.../sys_user_group/19e...}
```

Matching compares plain names, so `group_match` never fired. With
`MIN_SCORE=3` (two keywords, or one keyword plus a group match) this silently
discarded valid tickets. Measured on the current extract: **0 matches before
flattening, 10 after.** The 10/88 Wintel hit rate (~11%) lines up with the
97/907 (~11%) recorded for System 2, so the two are consistent once the field is
flattened.

`overlay/service.py::normalize_ticket()` flattens these on the way in. Note the
files in `output/` contain clean group names, so the extract that produced the
analysis had a different field format than the CSV now on disk â€” worth
confirming which extractor settings are current before re-running analysis.

Also fixed: the CSV reader hit `_csv.Error: field larger than field limit
(131072)` because `work_notes` can exceed Python's default cap. The limit is now
raised at import.

## Bug found and fixed during build

Timestamps store only to the second, so two approvals created in the same second
ordered non-deterministically â€” meaning a **stale approval could win and
authorise a plan the reviewer never saw**. Fixed by explicitly superseding prior
approvals on any new decision (`Store.supersede_approvals`), plus a `rowid`
tiebreaker.

## How to run

```powershell
python -m uvicorn overlay.api:app --host 127.0.0.1 --port 8800
# then open http://127.0.0.1:8800   (API docs at /docs)
```

## Staying convertible to a desktop app

The browser client holds no logic â€” it renders API responses and calls back, and
even fetches the risk table from `/api/risk-policy` rather than hardcoding it.
Wrapping it as a desktop app (Tauri/Electron) needs one line:

```html
<script>window.OVERLAY_API_BASE = "https://overlay.internal.example.com";</script>
```

plus allowing that origin via `OVERLAY_ALLOWED_ORIGINS`. No build step, no
bundler. What must **never** move into a desktop client: credentials, approval
or risk decisions, run state, the audit log.

## Verified

9/9 endpoint and control checks pass. A full ticket lifecycle reaches
`completed` 7/7 steps. An unmatched ticket is refused with 422 and stays with a
human. Connectors disabled; `system2/executor.live_execute` still raises.

## Phase 2: customisation + live data

Two additions, each with its own safety boundary.

**Uploadable knowledge.** Handbooks (`.json`, `.yaml`, `.csv`, `.md`) and
dictionaries (`.csv`, `.json`, `.yaml`, `.txt`) can be uploaded through the UI.

- A handbook produces **drafts**. Drafts are never loaded by the matcher.
  Publishing is the human gate and the only path into the live rule set.
- The review screen shows, per step, the risk class and owner that publishing
  would produce â€” so the reviewer approves a plan, not a filename.
- A file may *declare* a low risk to try to get a step automated. When the action
  is one whose safety cannot be verified (`run_command`, `reboot`,
  `delete_profile`) that claim is flagged as a warning before publishing.
- A **dictionary** maps a canonical term to real-world phrasings and takes effect
  immediately, because it can only widen *matching* â€” never permission.
  Measured: with the example dictionary, *"User cannot login, account is locked
  out"* matches the unlock procedure at score 7.0; without it, nothing matches.

**Live ticket feed (read-only).** Polls ServiceNow into an inbox.

- `_get()` is the only network call in `live.py` and issues **GET only**. No code
  path writes to ServiceNow.
- Reading live data does **not** enable live execution. Runs still need approval
  and steps are still simulated.
- Credentials come from the environment, never the database, and the API reports
  only whether they are present.
- Dedupes on `sys_id`, so re-polling never duplicates a ticket or resets a triage
  decision.
- Default query is `active=true^u_record_type=incident^contact_type!=Automated`.
  The `u_record_type` clause is required on this instance: several record types
  share the incident table, and without it the feed also returns `RQ` records
  (found during testing).

Verified against the live instance: 31/31 API checks pass, including
upload â†’ draft â†’ *not active* â†’ publish â†’ matches, an uploaded procedure still
refusing execution without approval, and a real poll of 50 current tickets.

## Open / next steps (System 3)

- [x] **Auth.** Entra ID / OIDC with real RS256 JWKS verification (issuer,
      audience, expiry, nonce) in `auth.py`; `X-Actor` is ignored when
      `BUCKY_ENFORCE_AUTH=1`. Server-side capability RBAC gates every write,
      including publishing. (Dev-login fallback only when Entra is unconfigured.)
- [x] **Separation of duties.** `execute_chunk` refuses to let the approver also
      trigger execution (`BUCKY_ENFORCE_SOD`, default on); the audit records
      `approved_by` vs `triggered_by`. Verified live.
- [x] **Approval expiry.** Approvals expire after
      `BUCKY_APPROVAL_TTL_SECONDS` (default 1h); an expired approval is refused.
- [x] **Transactional server DB + leases.** PostgreSQL store
      (`postgres_store.py`) with migrations, optimistic concurrency, and named
      leases; backend chosen by `BUCKY_STORE_BACKEND`. SQLite remains the local
      default.
- [ ] Live polling is a recent-window read, not a backfill. A ticket updated
      while the feed was off is only picked up if still in the recent window.
- [ ] Classify safe `run_command` steps explicitly (e.g. `"risk": "R0"` on a
      read-only `Get-PSDrive`) to grow agent coverage â€” a `procedures.json`
      edit, no code change.
- [x] Background worker. `worker.py` (leader-leased loop) + `worker_main.py`
      (ECS entrypoint) consume SQS and report to Step Functions; the producer
      seam (`service.dispatch_chunk_async`) enqueues approved chunks. The API
      can still run synchronously; async is now available behind the same
      `execute_chunk` path.
- [x] Rollback execution â€” the `rollback` field now threads into `StepRun` and
      the **compensation seam** (`overlay/rollback.py`) attempts it when a
      postcondition cannot be confirmed; fail-closed for real effects. Running a
      real compensator awaits a live connector.
- [ ] Layer C connectors behind the approval gate â€” still a separate,
      deliberate project. The one real connector (ServiceNow work-note) stays
      dual-switch-gated and off by default.

---

# Roadmap progress â€” architecture review items

_Updated: 2026-09-28._

Tracking the external architecture review's P0/P1 items against the code as it
actually is now (verified, tested, committed). This section exists so solved
work is not re-tracked as open.

## P0 â€” identity, authorization, production safety â€” DONE

The review's P0 described an older state of the repo. In the current code:

- Identity is a signed session from **Entra ID / OIDC** with real RS256 JWKS
  verification (issuer, audience, expiry, nonce). `X-Actor` is ignored under
  `BUCKY_ENFORCE_AUTH=1`.
- **Capability RBAC** (viewer/operator/approver/admin) is enforced server-side
  on every write, publishing included.
- **Separation of duties**, **expiring approvals**, **idempotency keys**, and
  **version-bound approvals** (binding digest over procedure+catalog+rendered+
  risk+owner+params) are all enforced in `execute_chunk` and tested.
- Destructive/unclassified actions stay human-only (`risk.py`, default-deny;
  `R2` isolated into its own single-step approval).

Only remaining P0-adjacent gap: real-connector **rollback** (verification seam
is done; see P1 below).

## P1 â€” matching & recommendations â€” DONE (first increment)

- `service.recommend_candidates` + `POST /api/recommend-candidates`: **ranked,
  evidence-cited candidates with an abstain path.** Fuses the authoritative
  keyword matcher and the advisory semantic matcher into a 0..1 fused
  confidence per candidate, each citing matched keywords, assignment-group
  match, resolved/missing parameters, and which signals fired.
- Two added high-value signals: **error_code** (Event IDs, exit codes, HTTP
  status, hex, ORA-/E#### â€” a +0.25 boost on a shared code) and
  **historical_resolution** (corroboration from similar *solved* tickets via
  the knowledge base; closure notes are evidence a human inspects, never proof).
- **Abstains** (`decision = insufficient_evidence`) when the top candidate is
  below `BUCKY_RECOMMEND_MIN_CONFIDENCE` (default 0.45). A missed suggestion is
  safe; a confident wrong one is not.
- Strictly advisory: proposes/approves/executes nothing; the keyword engine
  stays authoritative for propose/execute.
- **Calibration harness** `scripts/07_calibrate_recommendations.py`: labeled
  set â†’ precision@1, recall@1, abstain rate, abstain correctness, signal
  coverage. Cases whose expected procedure is not loaded are reported as
  `not_loaded` and excluded (so the metric reflects ranking, not coverage
  artifacts). Baseline where procedures are present: precision@1 1.00,
  recall@1 1.00, abstain correctness 1.00.

Added since: **assignment-group and configuration-item are now first-class
signals** (CI matches the resolved host, ignoring domain suffix); **near-
duplicate resolution clustering** (`cluster_resolutions` +
`POST /api/resolution-clusters`) groups solved-ticket resolutions into recurring
patterns with distinct-incident counts. Still open: expanding the labeled set
group by group against real tickets.

## P1 â€” durable execution â€” DONE (first increment)

- The `action_executions` ledger already gave durable, **at-most-once**
  dispatch: an idempotency key is claimed atomically before the connector runs,
  and `execute_chunk` replays a succeeded record instead of dispatching twice.
- **Postcondition verification** (`overlay/verification.py`): after a connector
  reports success, the action's postcondition is confirmed before the step is
  marked succeeded. Simulated results get a pure consistency check; **real
  results with no registered check FAIL CLOSED** (â†’ `NEEDS_HUMAN`, never a
  false success). The step audit now carries the verification record.
- **Crash recovery** (`service.recover_stale_executions` â†’
  `store.reclaim_stale_executions`): a claim orphaned by a dead worker (stuck
  in `claimed` past `BUCKY_EXECUTION_CLAIM_TIMEOUT_SECONDS`, default 900s) is
  **failed, never silently retried**, so the step surfaces for a human. Closes
  the one crash window between claim and terminal outcome while preserving
  at-most-once.

Added since: **SQS + Step Functions orchestration is now complete end to end** â€”
the consumer (worker loop, SQS/SFN transports, ECS entrypoint) plus the new
**producer seam** (`service.dispatch_chunk_async` + `TaskProducer` /
`SqsTaskProducer`), so an approved chunk can be enqueued (FIFO, de-duped) and
run by the worker through the same `execute_chunk` path, exercisable fully
offline. **Rollback / compensation** is in place too (`overlay/rollback.py`):
when a postcondition cannot be confirmed, a declared compensation is attempted
before handoff; real effects with no live compensator fail closed. Still open:
wiring the live SQS producer into the API's execute route behind a flag, and
registering real per-connector verifiers/compensators when a live connector is
enabled.

## Test + quality status

- Suite: **158 passed, 10 skipped** (10 skips need a live PostgreSQL DSN).
- `ruff` clean across `overlay/ tests/ scripts/` (the lint CI job's invalid
  `W503` selector was removed; it now actually lints).
- Everything above is simulation-safe: no real connector is enabled, and the
  one real connector (ServiceNow work-note) remains dual-switch-gated and off.

---

# Read-only diagnostic gateway (Project BUCKY workstream D)

_Added: 2026-09-28._

A **separate, default-deny, read-only** evidence-gathering boundary â€”
independent of the simulation action gateway and the ServiceNow write
connector. This is the planning document's workstream D, built to its
non-negotiable guardrails.

- **`overlay/diagnostics.py`** â€” a fixed catalogue of reviewed read-only
  operations: `service_status`, `disk_free`, `dns_lookup`, `tcp_check`,
  `event_log_query`. The backend protocol has a single `read()` method; there
  is **no write path**.
- **Default-deny + no arbitrary commands** â€” an operation id outside the
  catalogue is refused; callers pass an operation id + validated parameters,
  never shell/PowerShell text. Hostname/IP validation rejects shell
  metacharacters; ports bound 1â€“65535; event entries capped at 50; output
  truncated.
- **Allowlists** (enforced on the real path): `BUCKY_DIAGNOSTICS_TARGETS` and
  `BUCKY_DIAGNOSTICS_PORTS`.
- **Dual-switch for the real backend**: `BUCKY_EXECUTION_MODE=live` **and**
  `BUCKY_DIAGNOSTICS_ENABLED=1`. Otherwise a deterministic mock answers (marked
  `simulated`), so the gateway runs fully offline and in tests. The real
  read-only backend (remote agent `Get-Service`/`Get-PSDrive`/`Resolve-DnsName`
  /`Test-NetConnection`, or SSM for managed nodes) is intentionally not wired
  yet and **fails closed**.
- **API**: `GET /api/diagnostics/catalog` (open) and `POST /api/diagnostics/run`
  (new `run_diagnostic` capability, operator+). Every run â€” and every refusal â€”
  is audited with requester, operation, target, and ticket ref.

Still read-only MVP: no remediation, no writes, no ticket updates. Verified
live (mock): catalog + `service_status` succeed; an unknown operation and an
injection attempt in the target are both refused with 409. Suite **172 passed**.

This also lays the safe foundation for the remote-target idea discussed: a
read-only `service_status` against an allowlisted host is the first, risk-free
step toward a future (separate, explicitly-approved) remediation phase.

---

# Investigation workspace and recommendation feedback

The React application now includes an **Investigate** tab built around a ticket
case file rather than a chat transcript. It separates ServiceNow facts,
historical retrieval, deterministic recommendations, read-only diagnostics,
Opus advice, limitations, governance, and human validation by provenance.

- `GET /api/live/tickets/{live_id}/evidence-pack` composes a deterministic
  evidence pack and downloadable Markdown. Opus remains explicit opt-in through
  `include_opus=true`; generating the core pack does not make a paid model call.
- Diagnostic audit records now retain their bounded read-only evidence so the
  case file shows what was actually observed.
- Feedback is durable in the new `recommendation_feedback` table in both SQLite
  and PostgreSQL (migration `003_recommendation_feedback.sql`). The migration
  utility includes it in count/checksum verification.
- Operators can record accepted/partly useful/rejected/insufficient-evidence,
  useful and wrong steps, actual resolution, intervention, recurrence, notes,
  and estimated time saved. Aggregate endpoints report usefulness,
  intervention, and time-saving measures.
- Feedback remains evaluation-only: it cannot modify a procedure, authorize a
  run, or execute an action.

Validation: existing suite **172 passed, 10 skipped**, backend lint clean, React
production build succeeds, end-to-end API evidence-pack + feedback persistence
succeeds, and headless-browser validation renders 51 live tickets, five ranked
candidates, feedback controls, no console errors, and no literal ServiceNow
HTML in the facts ledger.
